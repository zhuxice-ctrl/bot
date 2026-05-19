"""
QQ 自动发卡机器人入口
"""
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
