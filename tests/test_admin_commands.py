import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init()

from nonebot.adapters.onebot.v11 import Message

from src.plugins.admin import get_command_text


class FakeEvent:
    def __init__(self, message: str):
        self.message = Message(message)

    def get_message(self):
        return self.message


def test_get_command_text_prefers_command_arg():
    text = get_command_text(
        FakeEvent("/切换 本地"),
        Message("上游"),
        {"切换", "switchmode"},
    )

    assert text == "上游"


def test_get_command_text_strips_raw_command_fallback():
    text = get_command_text(
        FakeEvent("/切换 上游"),
        Message(""),
        {"切换", "switchmode"},
    )

    assert text == "上游"
