"""抖音/短视频报告工作流插件。"""
from __future__ import annotations

import json

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule

from ..database import db
from .service import VideoReportService
from .workflow import (
    append_report_history,
    build_pending_review_reminder,
    build_report_context,
    chat_mode_setting_key,
    extract_preferred_video_url,
    find_report_in_history,
    format_report_detail,
    format_report_history_list,
    is_douyin_url,
    normalize_chat_mode,
)


shop_mode_cmd = on_command(
    "shop",
    aliases={"购物模式", "商品模式"},
    priority=4,
    block=True,
)

tk_mode_cmd = on_command(
    "tk",
    aliases={"视频模式", "报告模式"},
    priority=4,
    block=True,
)

video_report_cmd = on_command(
    "视频报告",
    aliases={"抖音报告", "分析视频"},
    priority=5,
    block=True,
)


review_report_cmd = on_command(
    "回顾",
    aliases={"报告回顾", "review"},
    priority=5,
    block=True,
)

report_list_cmd = on_command(
    "报告列表",
    aliases={"历史报告", "reportlist"},
    priority=5,
    block=True,
)

report_detail_cmd = on_command(
    "报告",
    aliases={"report"},
    priority=5,
    block=True,
)


@shop_mode_cmd.handle()
async def handle_shop_mode(event: MessageEvent):
    await _set_chat_mode(event, "shop")
    await shop_mode_cmd.finish("已切换到 /shop 商品模式。可以发送 /商品 查看商品，或发送 /帮助 查看菜单。")


@tk_mode_cmd.handle()
async def handle_tk_mode(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    await _set_chat_mode(event, "tk")
    text = args.extract_plain_text().strip() or str(event.get_message()).strip()
    url = extract_preferred_video_url(text)
    if not url:
        await tk_mode_cmd.finish("已切换到 /tk 视频报告模式。接下来可以直接发送抖音分享文本或链接。")
    await _run_video_report(bot, event, url, "已切换到 /tk 视频报告模式，并收到链接，已进入生成队列。")


@video_report_cmd.handle()
async def handle_video_report(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    text = args.extract_plain_text().strip() or str(event.get_message()).strip()
    url = extract_preferred_video_url(text)
    if not url:
        await video_report_cmd.finish("请发送: /视频报告 <抖音链接>")

    await _run_video_report(bot, event, url, "已收到链接，已进入视频报告生成队列。")


@review_report_cmd.handle()
async def handle_review_report(event: MessageEvent):
    pending = await _get_pending_review(event)
    if not pending:
        await review_report_cmd.finish("当前没有待回顾的视频报告。")
    await _clear_pending_review(event)
    await review_report_cmd.finish(
        "上一份待回顾报告:\n"
        f"标题: {pending.get('title') or '未命名视频'}\n"
        f"生成日期: {pending.get('generated_at_text') or '未知'}\n"
        f"报告编号: {pending.get('report_id') or '未知'}\n"
        f"链接: {pending.get('url') or '未知'}\n\n"
        "你可以继续围绕这条视频追问: 选题为什么成立、脚本结构怎么拆、账号适配度、可复刻模板、标题怎么改。"
    )


@report_list_cmd.handle()
async def handle_report_list(event: MessageEvent):
    history = await _get_report_history(event)
    await report_list_cmd.finish(format_report_history_list(history))


@report_detail_cmd.handle()
async def handle_report_detail(event: MessageEvent, args: Message = CommandArg()):
    query = args.extract_plain_text().strip()
    history = await _get_report_history(event)
    if not history:
        await report_detail_cmd.finish("暂无视频报告历史。")
    if not query:
        await report_detail_cmd.finish(format_report_history_list(history))
    item = find_report_in_history(history, query)
    if not item:
        await report_detail_cmd.finish("未找到对应报告。可发送 /报告列表 查看可用编号。")
    await report_detail_cmd.finish(format_report_detail(item))


async def _has_douyin_link(event: MessageEvent) -> bool:
    chat_mode = await _get_chat_mode(event)
    if isinstance(event, GroupMessageEvent) and not event.is_tome() and chat_mode != "tk":
        return False
    url = extract_preferred_video_url(str(event.get_message()))
    return bool(url and is_douyin_url(url))


douyin_link_msg = on_message(rule=Rule(_has_douyin_link), priority=20, block=True)


@douyin_link_msg.handle()
async def handle_douyin_link(bot: Bot, event: MessageEvent):
    url = extract_preferred_video_url(str(event.get_message()))
    await _run_video_report(bot, event, url, "检测到抖音链接，已进入视频报告生成队列。")


async def _run_video_report(bot: Bot, event: MessageEvent, url: str, received_message: str):
    await bot.send(event, received_message)
    previous = await _get_pending_review(event)
    if previous:
        await bot.send(event, build_pending_review_reminder(previous))
    result = await VideoReportService().generate(url)
    await _save_pending_review(event, result.index_info)
    await _save_report_context(event, result)
    await _send_report(bot, event, result.report)


async def _send_report(bot: Bot, event: MessageEvent, report: str):
    chunks = _split_message(report, 1800)
    for chunk in chunks[:-1]:
        await bot.send(event, chunk)
    await bot.send(event, chunks[-1] if chunks else "未生成报告")


def _split_message(text: str, limit: int) -> list[str]:
    clean = (text or "").strip()
    if not clean:
        return []
    return [clean[i:i + limit] for i in range(0, len(clean), limit)]


def _chat_scope(event: MessageEvent) -> tuple[str, str]:
    if isinstance(event, GroupMessageEvent):
        return "group", str(event.group_id)
    return "private", str(event.user_id)


def _mode_key(event: MessageEvent) -> str:
    scope, identifier = _chat_scope(event)
    return chat_mode_setting_key(scope, identifier)


def _pending_review_key(event: MessageEvent) -> str:
    scope, identifier = _chat_scope(event)
    return f"video_report:pending_review:{scope}:{identifier}"


def _report_context_key(event: MessageEvent) -> str:
    scope, identifier = _chat_scope(event)
    return f"video_report:last_context:{scope}:{identifier}"


def _report_history_key(event: MessageEvent) -> str:
    scope, identifier = _chat_scope(event)
    return f"video_report:history:{scope}:{identifier}"


async def _set_chat_mode(event: MessageEvent, mode: str):
    normalized = normalize_chat_mode(mode)
    if normalized:
        await db.set_setting(_mode_key(event), normalized)


async def _get_chat_mode(event: MessageEvent) -> str:
    saved = await db.get_setting(_mode_key(event))
    return normalize_chat_mode(saved) or "shop"


async def _save_pending_review(event: MessageEvent, index_info: dict[str, str]):
    await db.set_setting(_pending_review_key(event), json.dumps(index_info, ensure_ascii=False))


async def _save_report_context(event: MessageEvent, result):
    context = build_report_context(
        index_info=result.index_info,
        report=result.report,
        metadata_summary=result.metadata_summary,
        transcript=result.transcript,
        visual_text=result.visual_text,
        warnings=result.warnings,
    )
    await db.set_setting(_report_context_key(event), json.dumps(context, ensure_ascii=False))
    history = append_report_history(await _get_report_history(event), context)
    await db.set_setting(_report_history_key(event), json.dumps(history, ensure_ascii=False))


async def _get_pending_review(event: MessageEvent) -> dict[str, str] | None:
    raw = await db.get_setting(_pending_review_key(event))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _get_report_history(event: MessageEvent) -> list[dict]:
    raw = await db.get_setting(_report_history_key(event))
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


async def _clear_pending_review(event: MessageEvent):
    await db.set_setting(_pending_review_key(event), "")
