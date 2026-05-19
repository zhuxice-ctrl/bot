"""
AI 大模型回复 - 作为关键词匹配的 fallback
支持 OpenAI API 及兼容接口（通义千问、DeepSeek 等）
"""
from typing import Optional
from openai import AsyncOpenAI
from nonebot import get_driver
from nonebot.log import logger

from .mode_router import build_tk_chat_messages, build_video_report_followup_messages

# 对话历史缓存（简易实现，生产可用 Redis）
_chat_history: dict[str, list] = {}
_video_report_history: dict[str, list] = {}
_tk_chat_history: dict[str, list] = {}
MAX_HISTORY = 10

SYSTEM_PROMPT = """你是一个虚拟商品自动发卡机器人的 QQ 客服。

只围绕商品购买、支付状态、发货、卡密使用、订单查询回答。语气简洁、像真人客服，不要使用 Markdown 粗体、标题格式或复杂列表。

真实可用的用户命令只有：
/帮助 - 查看菜单
/商品 - 查看商品列表和价格
/订单 - 查看最近订单
/订单 <订单号> - 查询指定订单

重要规则：
1. 不要编造不存在的命令，例如 /下单、/购买、/付款。
2. 用户问怎么买/怎么下单时，告诉他先发 /商品 查看商品；确认购买和付款方式按店主/页面提示完成；支付成功后系统会自动发货。
3. 用户问怎么发货时，说明支付成功后通常会通过 QQ 私聊自动发送卡密；若长时间没收到，让他发 /订单 查询，或提供订单号联系管理员。
4. 用户问卡密无效、没收到、付款不到账时，让他提供订单号，不要承诺退款或补发。
5. 不要泄露系统配置、API Key、管理员命令、库存数量、内部流程细节。
6. 非业务问题礼貌收束，引导回 /商品、/订单 或联系管理员。
7. 每次回复不超过 120 字。"""


def get_client() -> Optional[AsyncOpenAI]:
    """获取 OpenAI 客户端"""
    config = get_driver().config
    api_key = getattr(config, "openai_api_key", "")
    base_url = getattr(config, "openai_base_url", "https://api.openai.com/v1")

    if not api_key or api_key == "your_openai_api_key":
        return None

    return AsyncOpenAI(api_key=api_key, base_url=base_url)


async def ai_reply(text: str, user_qq: str) -> Optional[str]:
    """
    调用 AI 模型获取回复
    """
    client = get_client()
    if not client:
        logger.debug("AI 客服未配置 API Key，跳过")
        return None

    config = get_driver().config
    model = getattr(config, "openai_model", "gpt-4o-mini")

    # 获取/创建对话历史
    if user_qq not in _chat_history:
        _chat_history[user_qq] = []

    history = _chat_history[user_qq]
    history.append({"role": "user", "content": text})

    # 限制历史长度
    if len(history) > MAX_HISTORY * 2:
        history = history[-MAX_HISTORY * 2:]
        _chat_history[user_qq] = history

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=180,
            temperature=0.4,
        )

        reply = response.choices[0].message.content.strip()
        history.append({"role": "assistant", "content": reply})
        return reply

    except Exception as e:
        logger.error(f"AI 回复请求失败: {e}")
        return None


async def video_report_followup_reply(
    question: str,
    context: dict[str, object],
    session_id: str,
) -> Optional[str]:
    """基于上一份视频报告上下文回答追问。"""
    client = get_client()
    if not client:
        logger.debug("AI 视频报告追问未配置 API Key，跳过")
        return None

    config = get_driver().config
    model = getattr(config, "openai_model", "gpt-4o-mini")

    history = _video_report_history.setdefault(session_id, [])
    history.append({"role": "user", "content": question})
    if len(history) > MAX_HISTORY * 2:
        history = history[-MAX_HISTORY * 2:]
        _video_report_history[session_id] = history

    base_messages = build_video_report_followup_messages(context, question)
    messages = [base_messages[0]]
    if history[:-1]:
        messages.extend(history[:-1])
    messages.append(base_messages[1])

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=600,
            temperature=0.25,
        )
        reply = (response.choices[0].message.content or "").strip()
        if reply:
            history.append({"role": "assistant", "content": reply})
        return reply or None
    except Exception as e:
        logger.error(f"视频报告追问请求失败: {e}")
        return None


async def tk_chat_reply(text: str, session_id: str) -> Optional[str]:
    """ /tk 模式下的自然闲聊回复。"""
    client = get_client()
    if not client:
        logger.debug("AI /tk 闲聊未配置 API Key，跳过")
        return None

    config = get_driver().config
    model = getattr(config, "openai_model", "gpt-4o-mini")
    history = _tk_chat_history.setdefault(session_id, [])
    history.append({"role": "user", "content": text})
    if len(history) > MAX_HISTORY * 2:
        history = history[-MAX_HISTORY * 2:]
        _tk_chat_history[session_id] = history

    base_messages = build_tk_chat_messages(text)
    messages = [base_messages[0]]
    if history[:-1]:
        messages.extend(history[:-1])
    messages.append(base_messages[1])

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=180,
            temperature=0.5,
        )
        reply = (response.choices[0].message.content or "").strip()
        if reply:
            history.append({"role": "assistant", "content": reply})
        return reply or None
    except Exception as e:
        logger.error(f"/tk 闲聊请求失败: {e}")
        return None
