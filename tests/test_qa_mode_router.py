import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init()

from src.plugins.qa.mode_router import (
    build_tk_mode_reply,
    build_video_report_fallback_reply,
    build_video_report_followup_messages,
)


def test_build_tk_mode_reply_describes_video_report_mode_not_shop_mode():
    reply = build_tk_mode_reply("你现在会什么")

    assert "/tk 视频报告模式" in reply
    assert "抖音分享文本或链接" in reply
    assert "生成日期" in reply
    assert "/回顾" in reply
    assert "/shop" in reply
    assert "发 /商品" not in reply


def test_build_video_report_followup_messages_use_report_context_and_visible_logic_chain():
    context = {
        "title": "AI 工具选择",
        "report": "核心观点: 程序员使用多种 AI 工具，引导评论区讨论。",
        "metadata_summary": "标题: AI 工具选择\n作者: 程序员牛牛",
        "transcript": "[0.0-3.0] 你平常使用什么 AI 工具",
    }

    messages = build_video_report_followup_messages(context, "为什么不是大模型回复我")
    combined = "\n".join(item["content"] for item in messages)

    assert "AI 工具选择" in combined
    assert "程序员使用多种 AI 工具" in combined
    assert "你平常使用什么 AI 工具" in combined
    assert "结论" in combined
    assert "依据" in combined
    assert "不要输出内部思维链" in combined


def test_build_video_report_fallback_reply_answers_from_context_summary():
    context = {
        "title": "AI 工具选择",
        "report_id": "VR-1",
        "report": "核心观点: 用 AI 工具话题引导程序员互动。",
    }

    reply = build_video_report_fallback_reply(context, "为什么能互动")

    assert "AI 工具选择" in reply
    assert "VR-1" in reply
    assert "核心观点" in reply
