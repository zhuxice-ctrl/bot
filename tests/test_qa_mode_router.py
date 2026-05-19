import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init()

from src.plugins.qa.mode_router import build_tk_mode_reply


def test_build_tk_mode_reply_describes_video_report_mode_not_shop_mode():
    reply = build_tk_mode_reply("你现在会什么")

    assert "/tk 视频报告模式" in reply
    assert "抖音分享文本或链接" in reply
    assert "生成日期" in reply
    assert "/回顾" in reply
    assert "/shop" in reply
    assert "发 /商品" not in reply
