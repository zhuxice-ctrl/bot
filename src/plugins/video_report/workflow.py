"""视频报告工作流的纯函数。"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any


URL_RE = re.compile(r"https?://[^\s<>\"'，。！？；：、]+", re.IGNORECASE)
TRAILING_PUNCTUATION = "，。！？；：、,.!?;:)]}）】》"
CHAT_MODE_ALIASES = {
    "shop": "shop",
    "/shop": "shop",
    "商品": "shop",
    "购物": "shop",
    "发货": "shop",
    "tk": "tk",
    "/tk": "tk",
    "视频": "tk",
    "报告": "tk",
    "抖音": "tk",
}


def extract_first_url(text: str) -> str:
    """从消息文本中提取第一个 URL。"""
    match = URL_RE.search(text or "")
    if not match:
        return ""
    return match.group(0).rstrip(TRAILING_PUNCTUATION)


def extract_preferred_video_url(text: str) -> str:
    """从分享文本中优先提取抖音链接，找不到时回退到第一个 URL。"""
    urls = [
        match.group(0).rstrip(TRAILING_PUNCTUATION)
        for match in URL_RE.finditer(text or "")
    ]
    for url in urls:
        if is_douyin_url(url):
            return url
    return urls[0] if urls else ""


def is_douyin_url(url: str) -> bool:
    """判断 URL 是否是抖音/抖音短链。"""
    lowered = (url or "").lower()
    return any(domain in lowered for domain in ("douyin.com", "iesdouyin.com"))


def normalize_chat_mode(text: object) -> str:
    """归一化会话大模式。"""
    clean = str(text or "").strip().lower()
    return CHAT_MODE_ALIASES.get(clean, "")


def chat_mode_setting_key(scope: str, identifier: str) -> str:
    """构造会话模式设置 key。"""
    return f"chat_mode:{scope}:{identifier}"


def summarize_metadata(info: dict[str, Any]) -> str:
    """把 yt-dlp 元数据整理成给模型读取的短文本。"""
    fields = [
        ("标题", info.get("title")),
        ("作者", info.get("uploader") or info.get("channel") or info.get("creator")),
        ("发布时间", info.get("upload_date") or info.get("timestamp")),
        ("时长", _format_duration(info.get("duration"))),
        ("链接", info.get("webpage_url") or info.get("original_url")),
        ("描述", info.get("description")),
        ("播放", info.get("view_count")),
        ("点赞", info.get("like_count")),
        ("评论", info.get("comment_count")),
        ("收藏", info.get("repost_count")),
    ]
    lines = []
    for label, value in fields:
        if value is None or value == "":
            continue
        lines.append(f"{label}: {_clean_text(str(value))}")
    return "\n".join(lines)


def build_report_header(info: dict[str, Any], generated_at: float | None = None) -> str:
    """构造稳定的检索信息头，避免让模型编造日期和标题。"""
    timestamp = generated_at if generated_at is not None else time.time()
    generated_at_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
    report_id = _build_report_id(info, timestamp)
    title = _clean_text(str(info.get("title") or info.get("description") or "未命名视频"))
    uploader = _clean_text(str(info.get("uploader") or info.get("channel") or info.get("creator") or "未知"))
    url = str(info.get("webpage_url") or info.get("original_url") or "")
    return (
        "短视频内容分析报告\n"
        "检索信息:\n"
        f"- 生成日期: {generated_at_text}\n"
        f"- 标题: {title}\n"
        f"- 作者: {uploader}\n"
        f"- 报告编号: {report_id}\n"
        f"- 视频链接: {url}\n"
    )


def build_pending_review_reminder(previous: dict[str, Any] | None) -> str:
    """生成上一份报告未回顾提醒。"""
    if not previous:
        return ""
    title = _clean_text(str(previous.get("title") or "未命名视频"))
    generated_at_text = _clean_text(str(previous.get("generated_at_text") or ""))
    report_id = _clean_text(str(previous.get("report_id") or ""))
    suffix = f"（{generated_at_text}）" if generated_at_text else ""
    report_id_text = f"，报告编号 {report_id}" if report_id else ""
    return (
        f"提醒: 上一份报告《{title}》{suffix}{report_id_text}还没进行细致追问。"
        "可以发送 /回顾 查看，或直接问我这条视频的选题、脚本、账号定位、可复用结构。"
    )


def build_report_messages(
    metadata_summary: str,
    transcript: str = "",
    visual_text: str = "",
) -> list[dict[str, str]]:
    """构造给 DeepSeek/OpenAI 兼容接口的报告提示词。"""
    system_prompt = (
        "你是短视频内容分析助理。根据用户提供的可验证材料生成报告，"
        "不要编造视频里没有出现的信息；如果缺少语音或画面证据，要明确说明信息不足。"
        "不要输出材料中没有提供的报告生成时间、发布时间或外部事实；涉及判断时标明依据来自元数据、语音转写或画面 OCR。"
        "如果语音转写中出现疑似错别字，不要直接当作事实，需标注为“疑似”。"
        "不要把“未识别到语音”直接判断为“内容缺失”，只能说“当前未获取到对应语音证据”。"
        "先在内部按 ReAct/CoT 风格完成证据核对和判断，但不要输出内部思维链；"
        "最终只输出可给用户阅读的结论、依据、不确定项和建议。"
        "报告要简洁、结构清晰，像运营复盘，不要像 AI 模板回复。"
    )
    user_prompt = (
        "请根据下面材料生成一份中文短视频分析报告。"
        "不要使用“好的”“根据您提供的材料”等开场白，第一行直接输出“短视频内容分析报告”。\n\n"
        "报告结构:\n"
        "1. 内容摘要\n"
        "2. 核心观点/卖点\n"
        "3. 目标受众\n"
        "4. 表达方式和节奏\n"
        "5. 可复用亮点\n"
        "6. 风险或信息不足\n\n"
        f"【元数据】\n{metadata_summary or '无'}\n\n"
        f"【语音转写】\n{transcript or '未获取到语音转写'}\n\n"
        f"【画面/OCR】\n{visual_text or '未获取到画面识别结果'}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_fallback_report(
    metadata_summary: str,
    transcript: str = "",
    warnings: list[str] | None = None,
) -> str:
    """AI 不可用时生成基础报告。"""
    warning_text = "\n".join(f"- {item}" for item in warnings or [])
    return (
        "视频报告\n"
        "━━━━━━━━━━━━━━\n"
        "内容摘要:\n"
        f"{_clip(transcript, 800) if transcript else '当前只获取到视频元数据，未获取到完整语音内容。'}\n\n"
        "元数据:\n"
        f"{metadata_summary or '无'}\n\n"
        "风险或信息不足:\n"
        f"{warning_text or '- 未接入 AI 报告模型或可读内容较少'}"
    )


def _format_duration(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{seconds} 秒"


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clip(text: str, limit: int) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "..."


def _build_report_id(info: dict[str, Any], timestamp: float) -> str:
    raw_video_id = str(info.get("id") or "").strip()
    if raw_video_id:
        suffix = re.sub(r"[^A-Za-z0-9_-]", "", raw_video_id)[:32]
    else:
        source = str(info.get("webpage_url") or info.get("original_url") or info.get("title") or timestamp)
        suffix = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
    date_part = time.strftime("%Y%m%d-%H%M%S", time.localtime(timestamp))
    return f"VR-{date_part}-{suffix}"
