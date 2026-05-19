"""
自动下单模块
收到支付成功通知后，自动到目标网站下单并获取卡密
"""
import asyncio
from nonebot.log import logger
from nonebot import get_bot, get_driver

from ..database import db
from .api_client import LdxpApiClient
from .browser import auto_purchase, create_order_to_payment, query_paid_order_card


async def process_paid_order(order_no: str, user_qq: str, product_id: str):
    """
    处理已支付订单：自动下单 -> 获取卡密 -> 发货
    """
    try:
        # 获取商品信息
        product = await db.get_product(product_id)
        if not product:
            logger.error(f"商品不存在: {product_id}, 订单: {order_no}")
            await db.update_order_status(order_no, "error", error_msg="商品不存在")
            await notify_admin(f"❌ 订单 {order_no} 失败: 商品 {product_id} 不存在")
            return

        logger.info(f"开始自动下单: 订单={order_no}, 商品={product['name']}, URL={product['target_url']}")

        mode = await get_active_fulfillment_mode()
        if mode == "manual":
            await db.update_order_status(order_no, "manual_pending")
            await notify_admin(build_manual_admin_message(order_no, user_qq, product))
            return

        if mode == "local_stock":
            await fulfill_from_local_stock(order_no, user_qq, product)
            return

        if mode == "manual_payment":
            await db.update_order_status(order_no, "creating_upstream_payment")
            result = await create_order_to_payment(
                target_url=product["target_url"],
                product_name=product["name"],
                order_no=order_no,
            )
            if result["success"]:
                await db.update_upstream_payment(
                    order_no,
                    upstream_trade_no=result.get("trade_no", ""),
                    upstream_pay_url=result.get("pay_url", ""),
                    status="waiting_upstream_payment",
                )
                await notify_admin(build_manual_payment_admin_message(order_no, user_qq, product, result))
            else:
                error_msg = result.get("error", "未知错误")
                await db.update_order_status(order_no, "error", error_msg=error_msg)
                await notify_admin(
                    f"❌ 订单 {order_no} 创建上游付款单失败\n"
                    f"用户: {user_qq}\n"
                    f"商品: {product['name']}\n"
                    f"错误: {error_msg}"
                )
            return

        # auto 模式：保留原先 API/浏览器全自动路径
        await db.update_order_status(order_no, "processing")
        result = await purchase_product(product, order_no)

        if result["success"]:
            card_key = result["card_key"]
            logger.info(f"自动下单成功: 订单={order_no}, 卡密={card_key[:20]}...")

            # 更新订单，记录卡密
            await db.update_order_status(order_no, "delivered", card_key=card_key)
            await db.increment_user_stats(user_qq, product["price"])

            # 发送卡密给用户
            await deliver_to_user(user_qq, order_no, product["name"], card_key)
        else:
            error_msg = result.get("error", "未知错误")
            logger.error(f"自动下单失败: 订单={order_no}, 错误={error_msg}")
            await db.update_order_status(order_no, "error", error_msg=error_msg)
            await notify_admin(
                f"❌ 订单 {order_no} 自动下单失败\n"
                f"用户: {user_qq}\n"
                f"商品: {product['name']}\n"
                f"错误: {error_msg}"
            )

    except Exception as e:
        logger.error(f"处理订单异常: {order_no}, {e}")
        await db.update_order_status(order_no, "error", error_msg=str(e))
        await notify_admin(f"❌ 订单 {order_no} 处理异常: {e}")


async def deliver_to_user(user_qq: str, order_no: str, product_name: str, card_key: str):
    """通过 QQ 私聊发送卡密给用户"""
    try:
        bot = get_bot()
        message = (
            f"✅ 订单发货成功！\n"
            f"━━━━━━━━━━━━━━\n"
            f"📦 商品: {product_name}\n"
            f"📋 订单号: {order_no}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔑 卡密/内容:\n{card_key}\n"
            f"━━━━━━━━━━━━━━\n"
            f"如有问题请发送 /help 查看帮助"
        )
        await bot.send_private_msg(user_id=int(user_qq), message=message)
        logger.info(f"卡密已发送给用户: {user_qq}")
    except Exception as e:
        logger.error(f"发送卡密失败: 用户={user_qq}, 错误={e}")
        await notify_admin(f"⚠️ 卡密发送失败\n用户: {user_qq}\n订单: {order_no}\n请手动处理")


async def fulfill_from_local_stock(order_no: str, user_qq: str, product: dict):
    """从本地卡密库存扣减并发货。"""
    await db.update_order_status(order_no, "processing")
    card_key = await db.reserve_card_stock(product["product_id"], order_no)

    if not card_key:
        error_msg = "本地卡密库存不足"
        await db.update_order_status(order_no, "error", error_msg=error_msg)
        await notify_admin(
            f"⚠️ 本地库存不足，订单未发货\n"
            f"订单号: {order_no}\n"
            f"用户: {user_qq}\n"
            f"商品: {product['name']} ({product['product_id']})\n"
            f"请导入卡密后使用:\n"
            f"/重试 {order_no}"
        )
        try:
            bot = get_bot()
            await bot.send_private_msg(
                user_id=int(user_qq),
                message=(
                    f"⚠️ 您的订单已支付，但当前库存不足，管理员已收到通知。\n"
                    f"订单号: {order_no}\n"
                    f"请稍后发送 /订单 {order_no} 查看处理结果。"
                )
            )
        except Exception as e:
            logger.error(f"库存不足通知用户失败: 用户={user_qq}, 错误={e}")
        return

    await db.update_order_status(order_no, "delivered", card_key=card_key)
    await db.increment_user_stats(user_qq, product["price"])
    await deliver_to_user(user_qq, order_no, product["name"], card_key)


def get_fulfillment_mode(config) -> str:
    mode = str(getattr(config, "order_fulfillment_mode", "manual_payment") or "manual_payment").lower()
    return normalize_fulfillment_mode(mode) or "manual_payment"


async def get_active_fulfillment_mode() -> str:
    if hasattr(db, "get_setting"):
        saved_mode = await db.get_setting("order_fulfillment_mode")
        normalized = normalize_fulfillment_mode(saved_mode)
        if normalized:
            return normalized
    return get_fulfillment_mode(get_driver().config)


def normalize_fulfillment_mode(mode: object) -> str:
    text = str(mode or "").strip().lower()
    aliases = {
        "本地": "local_stock",
        "本地库存": "local_stock",
        "库存": "local_stock",
        "local": "local_stock",
        "local_stock": "local_stock",
        "上游": "manual_payment",
        "半自动": "manual_payment",
        "上游付款": "manual_payment",
        "manual_payment": "manual_payment",
        "手动": "manual",
        "manual": "manual",
        "自动": "auto",
        "余额": "auto",
        "auto": "auto",
    }
    return aliases.get(text, "")


def is_local_stock_product(product: dict) -> bool:
    return str(product.get("target_url", "")).lower().startswith("local://")


def build_manual_admin_message(order_no: str, user_qq: str, product: dict) -> str:
    return (
        f"🧾 新订单待手动处理\n"
        f"订单号: {order_no}\n"
        f"用户: {user_qq}\n"
        f"商品: {product['name']}\n"
        f"金额: ¥{product['price']:.2f}\n"
        f"请手动采购后使用:\n"
        f"/手动发货 {order_no} 卡密内容"
    )


def build_manual_payment_admin_message(order_no: str, user_qq: str, product: dict, result: dict) -> str:
    pay_url = result.get("pay_url", "")
    trade_no = result.get("trade_no", "")
    message = (
        f"💳 上游付款单已创建\n"
        f"订单号: {order_no}\n"
        f"用户: {user_qq}\n"
        f"商品: {product['name']}\n"
        f"金额: ¥{product['price']:.2f}\n"
    )
    if trade_no:
        message += f"上游订单: {trade_no}\n"
    if pay_url:
        message += f"支付宝付款链接:\n{pay_url}\n"
    message += (
        f"付款完成后发送:\n"
        f"/检查发货 {order_no}\n"
        f"如果自动取卡失败，兜底使用:\n"
        f"/手动发货 {order_no} 卡密内容"
    )
    return message


async def check_manual_payment_order(order_no: str) -> dict:
    """管理员付款后检查上游订单并自动发货。"""
    order = await db.get_order(order_no)
    if not order:
        return {"success": False, "error": f"订单 {order_no} 不存在"}

    upstream_trade_no = order.get("upstream_trade_no", "")
    if not upstream_trade_no:
        return {"success": False, "error": f"订单 {order_no} 没有上游订单号，请手动发货"}

    product = await db.get_product(order["product_id"])
    product_name = product["name"] if product else order.get("product_name") or order["product_id"]
    product_price = product["price"] if product else order["amount"]

    result = await query_paid_order_card(upstream_trade_no, order_no=order_no)
    if not result.get("success"):
        return result

    card_key = result["card_key"]
    await db.update_order_status(order_no, "delivered", card_key=card_key)
    await db.increment_user_stats(order["user_qq"], product_price)
    await deliver_to_user(order["user_qq"], order_no, product_name, card_key)
    return {"success": True, "card_key": card_key}


async def purchase_product(product: dict, order_no: str) -> dict:
    """优先尝试 API 下单，失败后回退到浏览器自动化。"""
    api_result = None
    try:
        config = get_driver().config
        contact = getattr(config, "contact_email", "")
        async with LdxpApiClient() as client:
            api_result = await client.buy_with_balance(
                product["target_url"],
                contact=contact,
            )
        if api_result.get("success"):
            logger.info(f"API 直调下单成功: 订单={order_no}")
            return api_result
        if api_result.get("error") != "Ldxp API client disabled":
            logger.info(f"API 直调未完成，回退浏览器: 订单={order_no}, 错误={api_result.get('error')}")
    except Exception as e:
        logger.warning(f"API 直调异常，回退浏览器: 订单={order_no}, 错误={e}")

    return await auto_purchase(
        target_url=product["target_url"],
        product_name=product["name"],
        order_no=order_no,
    )


async def notify_admin(message: str):
    """通知管理员"""
    try:
        from nonebot import get_driver
        config = get_driver().config
        admin_qq = getattr(config, "admin_qq", None)
        if admin_qq:
            bot = get_bot()
            await bot.send_private_msg(user_id=int(admin_qq), message=message)
    except Exception as e:
        logger.error(f"通知管理员失败: {e}")
