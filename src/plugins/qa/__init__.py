"""
用户问答模块
- 关键词匹配优先
- 匹配不到走 AI 大模型
- 支持查询订单状态
"""
import json
from pathlib import Path
from nonebot import on_message, on_command
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, PrivateMessageEvent, GroupMessageEvent
from nonebot.log import logger

from .keywords import match_keyword
from .ai_chat import ai_reply
from .mode_router import build_tk_mode_reply
from ..database import db

# === 命令处理器 ===

# 查询订单
order_query = on_command("订单", aliases={"查单", "order"}, priority=5, block=True)


@order_query.handle()
async def handle_order_query(bot: Bot, event: MessageEvent):
    user_qq = str(event.user_id)
    args = str(event.get_message()).strip()

    if args:
        # 查询指定订单
        order = await db.get_order(args)
        if order and order["user_qq"] == user_qq:
            status_map = {
                "paid": "已支付，等待处理",
                "processing": "正在自动下单中...",
                "delivered": "已发货",
                "error": "处理异常，请联系客服",
            }
            status_text = status_map.get(order["status"], order["status"])
            msg = (
                f"📋 订单查询结果\n"
                f"订单号: {order['order_no']}\n"
                f"状态: {status_text}\n"
                f"金额: ¥{order['amount']:.2f}\n"
            )
            if order["status"] == "delivered" and order["card_key"]:
                msg += f"卡密: {order['card_key']}\n"
            if order["status"] == "error" and order["error_msg"]:
                msg += f"错误信息: {order['error_msg']}\n"
            await order_query.finish(msg)
        else:
            await order_query.finish("❌ 未找到该订单或无权查看")
    else:
        # 查询最近订单
        orders = await db.get_user_orders(user_qq, limit=5)
        if not orders:
            await order_query.finish("📭 暂无订单记录")
        else:
            msg = "📋 最近订单:\n"
            for o in orders:
                status_emoji = {"paid": "💰", "processing": "⏳",
                                "delivered": "✅", "error": "❌"}.get(o["status"], "❓")
                msg += f"{status_emoji} {o['order_no'][:12]}... | ¥{o['amount']:.2f} | {o['status']}\n"
            msg += "\n发送 /订单 <订单号> 查看详情"
            await order_query.finish(msg)


# 帮助命令
help_cmd = on_command("help", aliases={"帮助", "菜单"}, priority=5, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: MessageEvent):
    products = await db.get_all_products()
    product_list = ""
    for p in products:
        product_list += f"  • {p['name']} - ¥{p['price']:.2f}\n"

    msg = (
        "🤖 自动发卡机器人\n"
        "━━━━━━━━━━━━━━\n"
        "📦 可用商品:\n"
        f"{product_list or '  暂无商品'}\n"
        "━━━━━━━━━━━━━━\n"
        "📌 常用命令:\n"
        "  /帮助 - 查看本菜单\n"
        "  /订单 - 查看最近订单\n"
        "  /订单 <订单号> - 查询订单详情\n"
        "  /商品 - 查看所有商品\n"
        "━━━━━━━━━━━━━━\n"
        "💬 有任何问题可以直接提问"
    )
    await help_cmd.finish(msg)


# 商品列表
product_list_cmd = on_command("商品", aliases={"products", "列表"}, priority=5, block=True)


@product_list_cmd.handle()
async def handle_product_list(bot: Bot, event: MessageEvent):
    products = await db.get_all_products()
    if not products:
        await product_list_cmd.finish("📭 暂无可用商品")
    msg = "📦 商品列表:\n━━━━━━━━━━━━━━\n"
    for p in products:
        msg += f"🏷️ {p['name']}\n   价格: ¥{p['price']:.2f}\n   ID: {p['product_id']}\n\n"
    await product_list_cmd.finish(msg)


# === 通用消息处理（关键词 + AI） ===
general_msg = on_message(priority=99, block=True)


@general_msg.handle()
async def handle_general(bot: Bot, event: MessageEvent):
    text = str(event.get_message()).strip()
    if not text or text.startswith("/"):
        return

    chat_mode = await _get_chat_mode(event)

    # 群消息默认需要 @机器人；/tk 视频报告模式下允许继续对话
    if isinstance(event, GroupMessageEvent):
        if not event.is_tome() and chat_mode != "tk":
            return

    user_qq = str(event.user_id)

    if chat_mode == "tk":
        await general_msg.finish(build_tk_mode_reply(text))
        return

    # 1. 关键词匹配
    keyword_reply = await match_keyword(text)
    if keyword_reply:
        await general_msg.finish(keyword_reply)
        return

    # 2. AI 回复
    reply = None
    try:
        reply = await ai_reply(text, user_qq)
    except Exception as e:
        logger.error(f"AI 回复异常: {e}")
        await general_msg.finish("抱歉，暂时无法回复您的问题，请稍后再试或联系管理员。")

    if reply:
        await general_msg.finish(reply)


def _chat_scope(event: MessageEvent) -> tuple[str, str]:
    if isinstance(event, GroupMessageEvent):
        return "group", str(event.group_id)
    return "private", str(event.user_id)


async def _get_chat_mode(event: MessageEvent) -> str:
    scope, identifier = _chat_scope(event)
    saved = await db.get_setting(f"chat_mode:{scope}:{identifier}")
    return saved if saved in {"shop", "tk"} else "shop"
