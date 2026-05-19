"""视频下载、转写和报告生成服务。"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from nonebot import get_driver
from nonebot.log import logger
from openai import AsyncOpenAI

from .douyin_browser import DouyinBrowserConfig, DouyinBrowserResolver
from .workflow import build_fallback_report, build_report_header, build_report_messages, summarize_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_YTDLP_REPO = PROJECT_ROOT / "vendor" / "yt-dlp"
DEFAULT_WORK_DIR = PROJECT_ROOT / "data" / "video_reports"
DOUYIN_VIDEO_RE = re.compile(r"douyin\.com/video/(\d+)")
_REPORT_SEMAPHORE: asyncio.Semaphore | None = None
_REPORT_SEMAPHORE_LIMIT = 0


@dataclass
class VideoReportConfig:
    ytdlp_repo: Path = DEFAULT_YTDLP_REPO
    work_dir: Path = DEFAULT_WORK_DIR
    cookie_file: Path | None = None
    browser_resolve: bool = True
    browser_headless: bool = True
    browser_timeout_ms: int = 45000
    browser_executable_path: Path | None = None
    download_media: bool = True
    keep_media: bool = False
    whisper_model: str = "base"
    max_transcript_chars: int = 12000
    max_concurrency: int = 1

    @classmethod
    def from_nonebot(cls) -> "VideoReportConfig":
        config = get_driver().config
        return cls(
            ytdlp_repo=Path(str(getattr(config, "video_report_ytdlp_repo", DEFAULT_YTDLP_REPO))),
            work_dir=Path(str(getattr(config, "video_report_work_dir", DEFAULT_WORK_DIR))),
            cookie_file=_optional_path(getattr(config, "video_report_cookie_file", "")),
            browser_resolve=_as_bool(getattr(config, "video_report_browser_resolve", True)),
            browser_headless=_as_bool(getattr(config, "video_report_browser_headless", True)),
            browser_timeout_ms=int(getattr(config, "video_report_browser_timeout_ms", 45000) or 45000),
            browser_executable_path=_optional_path(
                getattr(config, "video_report_browser_executable_path", "")
            ),
            download_media=_as_bool(getattr(config, "video_report_download_media", True)),
            keep_media=_as_bool(getattr(config, "video_report_keep_media", False)),
            whisper_model=str(getattr(config, "video_report_whisper_model", "base") or "base"),
            max_transcript_chars=int(getattr(config, "video_report_max_transcript_chars", 12000) or 12000),
            max_concurrency=max(1, int(getattr(config, "video_report_max_concurrency", 1) or 1)),
        )


@dataclass
class VideoReportResult:
    report: str
    metadata_summary: str
    transcript: str
    warnings: list[str]
    index_info: dict[str, str]


class YtDlpClient:
    """优先使用本地克隆仓库，失败时回退到已安装的 yt_dlp 模块。"""

    def __init__(
        self,
        repo_path: Path,
        cookie_file: Path | None = None,
        douyin_cache_dir: Path | None = None,
    ):
        self.repo_path = repo_path
        self.cookie_file = cookie_file
        self.douyin_cache_dir = douyin_cache_dir

    async def fetch_metadata(self, url: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._extract_info_sync, url, False, None)

    async def download_media(self, url: str, work_dir: Path) -> Path:
        info = await asyncio.to_thread(self._extract_info_sync, url, True, work_dir)
        return _find_downloaded_file(info, work_dir)

    def _extract_info_sync(self, url: str, download: bool, work_dir: Path | None) -> dict[str, Any]:
        cached = _load_cached_douyin_info(url, self.douyin_cache_dir)
        if cached:
            if download and work_dir:
                cached = dict(cached)
                cached["requested_downloads"] = [
                    {"filepath": str(_download_cached_douyin_media(cached, work_dir))}
                ]
            return cached

        yt_dlp = self._load_ytdlp()
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "ignoreerrors": False,
            "retries": 2,
            "socket_timeout": 20,
        }
        if self.cookie_file and self.cookie_file.exists():
            ydl_opts["cookiefile"] = str(self.cookie_file)
        if download and work_dir:
            work_dir.mkdir(parents=True, exist_ok=True)
            ydl_opts.update(
                {
                    "format": "bestaudio/best",
                    "outtmpl": str(work_dir / "%(extractor)s_%(id)s.%(ext)s"),
                }
            )
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=download)
        except Exception as exc:
            if "Fresh cookies" not in str(exc):
                raise
            raise

    def _load_ytdlp(self):
        package_dir = self.repo_path / "yt_dlp"
        if package_dir.exists():
            repo = str(self.repo_path)
            if repo not in sys.path:
                sys.path.insert(0, repo)
        return importlib.import_module("yt_dlp")


class VideoReportService:
    def __init__(self, config: VideoReportConfig | None = None):
        self.config = config or VideoReportConfig.from_nonebot()
        self.ytdlp = YtDlpClient(
            self.config.ytdlp_repo,
            self.config.cookie_file,
            self.config.work_dir / "_douyin_cache",
        )
        self.douyin_browser = DouyinBrowserResolver(
            DouyinBrowserConfig(
                cache_dir=self.config.work_dir / "_douyin_cache",
                cookie_file=self.config.cookie_file,
                enabled=self.config.browser_resolve,
                headless=self.config.browser_headless,
                timeout_ms=self.config.browser_timeout_ms,
                executable_path=self.config.browser_executable_path,
            )
        )

    async def generate(self, url: str) -> VideoReportResult:
        async with _get_report_semaphore(self.config.max_concurrency):
            return await self._generate_unlocked(url)

    async def _generate_unlocked(self, url: str) -> VideoReportResult:
        warnings: list[str] = []
        work_dir = self._make_work_dir()
        url = await self._prepare_douyin_cache(url, warnings)
        metadata = await self.ytdlp.fetch_metadata(url)
        metadata_summary = summarize_metadata(metadata)
        generated_at = time.time()
        header = build_report_header(metadata, generated_at)
        index_info = _build_index_info(metadata, generated_at)

        transcript = ""
        if self.config.download_media:
            if _has_faster_whisper():
                try:
                    media_path = await self.ytdlp.download_media(url, work_dir)
                    transcript = await asyncio.to_thread(
                        _transcribe_media,
                        media_path,
                        self.config.whisper_model,
                        self.config.max_transcript_chars,
                    )
                except Exception as exc:
                    logger.exception(f"视频语音转写失败: {exc}")
                    warnings.append(f"语音转写失败: {exc}")
            else:
                warnings.append("未安装 faster-whisper，已跳过语音转写和媒体下载")

        report_body = await _generate_ai_report(metadata_summary, transcript, warnings)
        report = _merge_report_header(header, report_body)
        if not self.config.keep_media:
            shutil.rmtree(work_dir, ignore_errors=True)

        return VideoReportResult(
            report=report,
            metadata_summary=metadata_summary,
            transcript=transcript,
            warnings=warnings,
            index_info=index_info,
        )

    def _make_work_dir(self) -> Path:
        name = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        path = self.config.work_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _prepare_douyin_cache(self, url: str, warnings: list[str]) -> str:
        if "douyin.com" not in (url or "").lower():
            return url
        try:
            return await self.douyin_browser.resolve(url)
        except Exception as exc:
            logger.warning(f"抖音浏览器解析失败，回退到 yt-dlp: {exc}")
            warnings.append(f"抖音浏览器解析失败: {exc}")
            return url


async def _generate_ai_report(metadata_summary: str, transcript: str, warnings: list[str]) -> str:
    client = _get_openai_client()
    if not client:
        return build_fallback_report(metadata_summary, transcript, warnings)

    config = get_driver().config
    model = getattr(config, "openai_model", "deepseek-chat")
    messages = build_report_messages(metadata_summary, transcript)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=1400,
        )
        content = response.choices[0].message.content or ""
        return content.strip() or build_fallback_report(metadata_summary, transcript, warnings)
    except Exception as exc:
        logger.error(f"视频报告模型调用失败: {exc}")
        warnings.append(f"AI 报告生成失败: {exc}")
        return build_fallback_report(metadata_summary, transcript, warnings)


def _get_openai_client() -> AsyncOpenAI | None:
    config = get_driver().config
    api_key = getattr(config, "openai_api_key", "")
    base_url = getattr(config, "openai_base_url", "https://api.openai.com/v1")
    if not api_key or api_key == "your_openai_api_key":
        return None
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def _get_report_semaphore(limit: int) -> asyncio.Semaphore:
    global _REPORT_SEMAPHORE, _REPORT_SEMAPHORE_LIMIT
    normalized = max(1, int(limit or 1))
    if _REPORT_SEMAPHORE is None or _REPORT_SEMAPHORE_LIMIT != normalized:
        _REPORT_SEMAPHORE = asyncio.Semaphore(normalized)
        _REPORT_SEMAPHORE_LIMIT = normalized
    return _REPORT_SEMAPHORE


def _merge_report_header(header: str, report_body: str) -> str:
    body = (report_body or "").strip()
    if body.startswith("短视频内容分析报告"):
        body = body[len("短视频内容分析报告") :].lstrip("\n\r -—")
    return f"{header.strip()}\n\n{body}".strip()


def _build_index_info(info: dict[str, Any], generated_at: float) -> dict[str, str]:
    header = build_report_header(info, generated_at)
    values: dict[str, str] = {
        "title": str(info.get("title") or info.get("description") or "未命名视频"),
        "url": str(info.get("webpage_url") or info.get("original_url") or ""),
    }
    for raw_line in header.splitlines():
        line = raw_line.strip()
        if line.startswith("- 生成日期:"):
            values["generated_at_text"] = line.split(":", 1)[1].strip()
        elif line.startswith("- 报告编号:"):
            values["report_id"] = line.split(":", 1)[1].strip()
    return values


def _has_faster_whisper() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _transcribe_media(media_path: Path, model_name: str, max_chars: int) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(media_path), vad_filter=True)
    lines: list[str] = []
    total = 0
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        line = f"[{segment.start:.1f}-{segment.end:.1f}] {text}"
        lines.append(line)
        total += len(line)
        if total >= max_chars:
            lines.append("...")
            break
    return "\n".join(lines)


def _find_downloaded_file(info: dict[str, Any], work_dir: Path) -> Path:
    for item in info.get("requested_downloads") or []:
        filepath = item.get("filepath") or item.get("_filename")
        if filepath and Path(filepath).exists():
            return Path(filepath)
    candidates = [p for p in work_dir.iterdir() if p.is_file()]
    if not candidates:
        raise FileNotFoundError("yt-dlp 未返回已下载媒体文件")
    return max(candidates, key=lambda p: p.stat().st_size)


def _load_cached_douyin_info(url: str, cache_dir: Path | None) -> dict[str, Any] | None:
    video_id = _douyin_video_id(url)
    if not video_id or not cache_dir:
        return None
    cache_file = cache_dir / f"{video_id}.json"
    if not cache_file.exists():
        return None
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    detail = data.get("aweme_detail") or data
    if not isinstance(detail, dict):
        return None
    return _douyin_detail_to_info(video_id, detail, url)


def _douyin_detail_to_info(video_id: str, detail: dict[str, Any], original_url: str) -> dict[str, Any]:
    author = detail.get("author") or {}
    video = detail.get("video") or {}
    stats = detail.get("statistics") or {}
    play_addr = video.get("play_addr") or {}
    cover = video.get("cover") or video.get("origin_cover") or {}
    duration_ms = video.get("duration")
    duration = None
    if isinstance(duration_ms, (int, float)):
        duration = int(duration_ms / 1000) if duration_ms > 1000 else int(duration_ms)
    return {
        "id": video_id,
        "extractor": "douyin_cache",
        "title": detail.get("desc") or video_id,
        "description": detail.get("desc") or "",
        "uploader": author.get("nickname") or author.get("unique_id") or "",
        "uploader_id": author.get("uid") or author.get("short_id") or "",
        "duration": duration,
        "timestamp": detail.get("create_time"),
        "webpage_url": original_url,
        "original_url": original_url,
        "view_count": stats.get("play_count"),
        "like_count": stats.get("digg_count"),
        "comment_count": stats.get("comment_count"),
        "repost_count": stats.get("share_count"),
        "thumbnail": _first_url(cover),
        "url": _first_url(play_addr),
        "ext": "mp4",
    }


def _download_cached_douyin_media(info: dict[str, Any], work_dir: Path) -> Path:
    media_url = str(info.get("url") or "")
    if not media_url:
        raise FileNotFoundError("抖音缓存中没有可用播放地址")
    work_dir.mkdir(parents=True, exist_ok=True)
    output = work_dir / f"douyin_{info.get('id') or int(time.time())}.mp4"
    request = Request(
        media_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
        },
    )
    with urlopen(request, timeout=60) as response, output.open("wb") as file:
        shutil.copyfileobj(response, file)
    return output


def _first_url(data: object) -> str:
    if isinstance(data, dict):
        urls = data.get("url_list") or []
        if urls:
            return str(urls[0])
        return str(data.get("url") or data.get("uri") or "")
    if isinstance(data, list) and data:
        return str(data[0])
    return ""


def _douyin_video_id(url: str) -> str:
    match = DOUYIN_VIDEO_RE.search(url or "")
    return match.group(1) if match else ""


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "开启"}


def _optional_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text)
