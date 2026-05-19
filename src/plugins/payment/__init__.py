"""
支付回调处理模块
接收支付宝/微信支付成功回调，触发自动下单流程
"""
from nonebot import get_driver, get_app
from nonebot.log import logger
from .webhook import setup_routes

try:
    driver = get_driver()
except ValueError:
    driver = None


if driver is not None:
    @driver.on_startup
    async def register_payment_routes():
        app = get_app()
        setup_routes(app)
        logger.info("支付回调路由已注册: /api/pay/alipay, /api/pay/wechat")
