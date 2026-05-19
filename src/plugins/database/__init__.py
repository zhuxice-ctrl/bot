"""数据库管理模块"""
from nonebot import get_driver
from .models import Database

db = Database()

try:
    driver = get_driver()
except ValueError:
    driver = None


if driver is not None:
    @driver.on_startup
    async def init_db():
        await db.init()


    @driver.on_shutdown
    async def close_db():
        await db.close()
