"""Douyin browser-backed metadata resolver."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DOUYIN_VIDEO_RE = re.compile(r"douyin\.com/video/(\d+)")


@dataclass
class DouyinBrowserConfig:
    cache_dir: Path
    cookie_file: Path | None = None
    enabled: bool = True
    headless: bool = True
    timeout_ms: int = 45000
    executable_path: Path | None = None


class DouyinBrowserResolver:
    def __init__(self, config: DouyinBrowserConfig):
        self.config = config

    async def resolve(self, url: str) -> str:
        """Resolve a Douyin page through a browser and cache aweme_detail JSON."""
        if not self.config.enabled:
            return url

        cached_id = extract_douyin_video_id(url)
        if cached_id and (self.config.cache_dir / f"{cached_id}.json").exists():
            return canonical_video_url(cached_id)

        from playwright.async_api import async_playwright

        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            launch_kwargs: dict[str, Any] = {
                "headless": self.config.headless,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if self.config.executable_path:
                launch_kwargs["executable_path"] = str(self.config.executable_path)
            browser = await p.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/148.0.0.0 Safari/537.36"
                    ),
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 720},
                )
                cookies = load_netscape_cookies(self.config.cookie_file)
                if cookies:
                    await context.add_cookies(cookies)
                page = await context.new_page()
                page.set_default_timeout(self.config.timeout_ms)
                await page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                video_id = extract_douyin_video_id(page.url) or cached_id
                if not video_id:
                    raise ValueError(f"未能从抖音页面解析视频 ID: {page.url}")
                detail = await fetch_aweme_detail(page, video_id)
                if not detail.get("aweme_detail"):
                    raise ValueError("抖音浏览器解析未返回 aweme_detail")
                cache_file = self.config.cache_dir / f"{video_id}.json"
                cache_file.write_text(
                    json.dumps(detail, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return canonical_video_url(video_id)
            finally:
                await browser.close()


async def fetch_aweme_detail(page: Any, video_id: str) -> dict[str, Any]:
    api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={video_id}"
    result = await page.evaluate(
        """
        async (apiUrl) => {
          const response = await fetch(apiUrl, {
            credentials: 'include',
            headers: {
              'accept': 'application/json, text/plain, */*',
              'referer': location.href
            }
          });
          const text = await response.text();
          return {status: response.status, text};
        }
        """,
        api_url,
    )
    if int(result.get("status") or 0) != 200:
        raise ValueError(f"抖音详情接口返回 HTTP {result.get('status')}")
    return json.loads(result.get("text") or "{}")


def load_netscape_cookies(cookie_file: Path | None) -> list[dict[str, Any]]:
    if not cookie_file or not cookie_file.exists():
        return []
    cookies: list[dict[str, Any]] = []
    for raw_line in cookie_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_") :]
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _include_subdomains, path, secure, expires, name, value = parts[:7]
        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "httpOnly": http_only,
            "secure": secure.upper() == "TRUE",
        }
        try:
            expires_value = int(float(expires))
        except ValueError:
            expires_value = 0
        if expires_value > 0:
            cookie["expires"] = expires_value
        cookies.append(cookie)
    return cookies


def extract_douyin_video_id(url: str) -> str:
    match = DOUYIN_VIDEO_RE.search(url or "")
    return match.group(1) if match else ""


def canonical_video_url(video_id: str) -> str:
    return f"https://www.douyin.com/video/{video_id}"
