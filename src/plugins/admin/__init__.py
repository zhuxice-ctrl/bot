"""
管理员命令模块
- 添加/管理商品
- 手动发货
- 查看统计
"""
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.log import logger

from ..database import db


MODE_LABELS = {
    "local_stock": "本地库存发货",
    "manual_payment": "上游半自动付款",
    "manual": "全手动处理",
    "auto": "余额自动下单",
}


def get_command_text(event: MessageEvent, args: Message, command_names: set[str]) -> str:
    text = args.extract_plain_text().strip()
    if text:
        return text

    raw = str(event.get_message()).strip()
    for name in command_names:
        for prefix in ("/", ""):
            command = f"{prefix}{name}"
            if raw == command:
                return ""
            if raw.startswith(command + " "):
                return raw[len(command):].strip()
    return raw


def build_mode_help(current_mode: str) -> str:
    current_label = MODE_LABELS.get(current_mode, current_mode or "未知")
    return (
        f"🔁 当前发货模式: {current_label} ({current_mode})\n"
        f"━━━━━━━━━━━━━━\n"
        f"可用命令:\n"
        f"/切换 本地  - 本地库存自动发货\n"
        f"/切换 上游  - 自动生成上游支付宝付款页\n"
        f"/切换 手动  - 只通知管理员手动处理\n"
        f"/切换 自动  - 余额自动下单模式"
    )


# === 切换发货模式 ===
switch_mode_cmd = on_command("切换", aliases={"switchmode"}, permission=SUPERUSER, priority=1, block=True)


@switch_mode_cmd.handle()
async def handle_switch_mode(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """切换履约模式: /切换 [本地|上游|手动|自动]"""
    from ..auto_order import get_active_fulfillment_mode, normalize_fulfillment_mode

    text = get_command_text(event, args, {"切换", "switchmode"})
    if not text:
        current_mode = await get_active_fulfillment_mode()
        await switch_mode_cmd.finish(build_mode_help(current_mode))

    mode = normalize_fulfillment_mode(text)
    if not mode:
        current_mode = await get_active_fulfillment_mode()
        await switch_mode_cmd.finish(
            f"❌ 不支持的模式: {text}\n"
            f"请使用: 本地 / 上游 / 手动 / 自动\n\n"
            f"{build_mode_help(current_mode)}"
        )

    await db.set_setting("order_fulfillment_mode", mode)
    await switch_mode_cmd.finish(
        f"✅ 发货模式已切换为: {MODE_LABELS[mode]} ({mode})\n"
        f"新支付成功的订单会立即按此模式处理。"
    )

# === 添加商品 ===
add_product_cmd = on_command("添加商品", aliases={"addproduct"}, permission=SUPERUSER, priority=1, block=True)


@add_product_cmd.handle()
async def handle_add_product(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    格式: /添加商品 <product_id> <名称> <价格> <目标URL> [描述]
    示例: /添加商品 vip30 月卡VIP 30 https://example.com/buy/vip 一个月VIP会员
    """
    text = get_command_text(event, args, {"添加商品", "addproduct"})
    parts = text.split(maxsplit=4)

    if len(parts) < 4:
        await add_product_cmd.finish(
            "❌ 格式错误\n"
            "用法: /添加商品 <ID> <名称> <价格> <URL> [描述]\n"
            "示例: /添加商品 vip30 月卡VIP 30 https://example.com/buy"
        )

    product_id = parts[0]
    name = parts[1]
    try:
        price = float(parts[2])
    except ValueError:
        await add_product_cmd.finish("❌ 价格格式错误，请输入数字")
        return

    target_url = parts[3]
    description = parts[4] if len(parts) > 4 else ""

    await db.add_product(product_id, name, price, target_url, description)
    await add_product_cmd.finish(
        f"✅ 商品已添加\n"
        f"ID: {product_id}\n"
        f"名称: {name}\n"
        f"价格: ¥{price:.2f}\n"
        f"URL: {target_url}"
    )


# === 商品录入帮助 ===
product_input_help_cmd = on_command(
    "录入帮助",
    aliases={"商品录入", "商品帮助"},
    permission=SUPERUSER,
    priority=1,
    block=True,
)


@product_input_help_cmd.handle()
async def handle_product_input_help(bot: Bot, event: MessageEvent):
    await product_input_help_cmd.finish(
        "🧾 商品和价格录入\n"
        "━━━━━━━━━━━━━━\n"
        "本地库存发货:\n"
        "/切换 本地\n"
        "/添加本地商品 <商品ID> <商品名> <价格> [描述]\n"
        "例: /添加本地商品 vip30 月卡VIP 30 一个月会员\n\n"
        "导入卡密:\n"
        "/导入卡密 vip30\n"
        "CARD-001\n"
        "CARD-002\n\n"
        "查看库存:\n"
        "/库存 vip30\n"
        "━━━━━━━━━━━━━━\n"
        "上游半自动发货:\n"
        "/切换 上游\n"
        "/添加商品 <商品ID> <商品名> <价格> <上游URL> [描述]\n"
        "例: /添加商品 vip30 月卡VIP 30 https://pay.ldxp.cn/shop/xxx 一个月会员\n"
        "━━━━━━━━━━━━━━\n"
        "注意: 商品ID建议用英文/数字；商品名不要带空格；价格直接写数字。"
    )


# === 添加本地库存商品 ===
add_local_product_cmd = on_command("添加本地商品", aliases={"addlocalproduct"}, permission=SUPERUSER, priority=1, block=True)


@add_local_product_cmd.handle()
async def handle_add_local_product(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    格式: /添加本地商品 <product_id> <名称> <价格> [描述]
    示例: /添加本地商品 vip30 月卡VIP 30 一个月VIP会员
    """
    text = get_command_text(event, args, {"添加本地商品", "addlocalproduct"})
    parts = text.split(maxsplit=3)

    if len(parts) < 3:
        await add_local_product_cmd.finish(
            "❌ 格式错误\n"
            "用法: /添加本地商品 <ID> <名称> <价格> [描述]\n"
            "示例: /添加本地商品 vip30 月卡VIP 30 一个月VIP会员"
        )

    product_id = parts[0]
    name = parts[1]
    try:
        price = float(parts[2])
    except ValueError:
        await add_local_product_cmd.finish("❌ 价格格式错误，请输入数字")
        return

    description = parts[3] if len(parts) > 3 else "本地库存发货"
    await db.add_product(product_id, name, price, "local://stock", description)
    await add_local_product_cmd.finish(
        f"✅ 本地商品已添加\n"
        f"ID: {product_id}\n"
        f"名称: {name}\n"
        f"价格: ¥{price:.2f}\n"
        f"下一步导入卡密:\n"
        f"/导入卡密 {product_id}\n卡密1\n卡密2"
    )


# === 导入本地卡密 ===
import_cards_cmd = on_command("导入卡密", aliases={"importcards"}, permission=SUPERUSER, priority=1, block=True)


@import_cards_cmd.handle()
async def handle_import_cards(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    格式:
    /导入卡密 <商品ID>
    卡密1
    卡密2
    """
    text = get_command_text(event, args, {"导入卡密", "importcards"})
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await import_cards_cmd.finish(
            "❌ 格式: /导入卡密 <商品ID> <卡密内容>\n"
            "建议一行一个卡密，例如:\n"
            "/导入卡密 vip30\n卡密1\n卡密2"
        )

    product_id = parts[0]
    product = await db.get_product(product_id)
    if not product:
        await import_cards_cmd.finish(f"❌ 商品 {product_id} 不存在或未启用")

    card_keys = [line.strip() for line in parts[1].splitlines() if line.strip()]
    inserted = await db.add_card_stock(product_id, card_keys)
    stock = await db.get_stock_count(product_id)
    await import_cards_cmd.finish(
        f"✅ 卡密导入完成\n"
        f"商品: {product['name']} ({product_id})\n"
        f"本次新增: {inserted}\n"
        f"当前库存: {stock}\n"
        f"重复卡密会自动跳过"
    )


# === 查看本地库存 ===
stock_cmd = on_command("库存", aliases={"stock"}, permission=SUPERUSER, priority=1, block=True)


@stock_cmd.handle()
async def handle_stock(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """查看库存: /库存 [商品ID]"""
    product_id = get_command_text(event, args, {"库存", "stock"})

    if product_id:
        product = await db.get_product(product_id)
        if not product:
            await stock_cmd.finish(f"❌ 商品 {product_id} 不存在或未启用")
        stock = await db.get_stock_count(product_id)
        await stock_cmd.finish(
            f"📦 库存\n"
            f"商品: {product['name']} ({product_id})\n"
            f"可用卡密: {stock}"
        )

    counts = await db.get_all_stock_counts()
    if not counts:
        await stock_cmd.finish("📭 暂无商品")

    msg = "📦 本地卡密库存\n━━━━━━━━━━━━━━\n"
    for item in counts:
        msg += f"{item['product_id']} | {item['name']} | {item['stock']} 张\n"
    msg += "━━━━━━━━━━━━━━\n发送 /库存 <商品ID> 查看单个商品"
    await stock_cmd.finish(msg)


# === 手动发货 ===
manual_deliver = on_command("手动发货", aliases={"deliver"}, permission=SUPERUSER, priority=1, block=True)


@manual_deliver.handle()
async def handle_manual_deliver(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    格式: /手动发货 <订单号> <卡密内容>
    """
    text = get_command_text(event, args, {"手动发货", "deliver"})
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await manual_deliver.finish("❌ 格式: /手动发货 <订单号> <卡密内容>")

    order_no = parts[0]
    card_key = parts[1]

    order = await db.get_order(order_no)
    if not order:
        await manual_deliver.finish(f"❌ 订单 {order_no} 不存在")

    await db.update_order_status(order_no, "delivered", card_key=card_key)

    # 发送给用户
    try:
        user_qq = order["user_qq"]
        msg = (
            f"✅ 订单发货成功！\n"
            f"━━━━━━━━━━━━━━\n"
            f"📋 订单号: {order_no}\n"
            f"🔑 卡密/内容:\n{card_key}\n"
            f"━━━━━━━━━━━━━━"
        )
        await bot.send_private_msg(user_id=int(user_qq), message=msg)
        await manual_deliver.finish(f"✅ 已发货给用户 {user_qq}")
    except Exception as e:
        await manual_deliver.finish(f"⚠️ 订单已更新但发送失败: {e}")


# === 统计信息 ===
stats_cmd = on_command("统计", aliases={"stats"}, permission=SUPERUSER, priority=1, block=True)


@stats_cmd.handle()
async def handle_stats(bot: Bot, event: MessageEvent):
    if not db.db:
        await stats_cmd.finish("❌ 数据库未初始化")
        return

    cursor = await db.db.execute("SELECT COUNT(*) as cnt FROM orders")
    total_orders = (await cursor.fetchone())[0]

    cursor = await db.db.execute("SELECT COUNT(*) as cnt FROM orders WHERE status='delivered'")
    delivered = (await cursor.fetchone())[0]

    cursor = await db.db.execute("SELECT COUNT(*) as cnt FROM orders WHERE status='error'")
    errors = (await cursor.fetchone())[0]

    cursor = await db.db.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status='delivered'")
    total_income = (await cursor.fetchone())[0]

    cursor = await db.db.execute(
        "SELECT COUNT(*) FROM orders WHERE status IN ('processing', 'creating_upstream_payment', 'waiting_upstream_payment', 'manual_pending')"
    )
    processing = (await cursor.fetchone())[0]

    await stats_cmd.finish(
        f"📊 系统统计\n"
        f"━━━━━━━━━━━━━━\n"
        f"📦 总订单: {total_orders}\n"
        f"✅ 已发货: {delivered}\n"
        f"⏳ 处理中: {processing}\n"
        f"❌ 异常: {errors}\n"
        f"💰 总收入: ¥{total_income:.2f}"
    )


# === 重试失败订单 ===
retry_cmd = on_command("重试", aliases={"retry"}, permission=SUPERUSER, priority=1, block=True)


@retry_cmd.handle()
async def handle_retry(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """重新处理失败订单: /重试 <订单号>"""
    order_no = get_command_text(event, args, {"重试", "retry"})
    if not order_no:
        await retry_cmd.finish("❌ 格式: /重试 <订单号>")

    order = await db.get_order(order_no)
    if not order:
        await retry_cmd.finish(f"❌ 订单 {order_no} 不存在")

    if order["status"] not in ("error", "paid"):
        await retry_cmd.finish(f"❌ 订单状态为 {order['status']}，无法重试")

    from ..auto_order import process_paid_order
    await retry_cmd.send(f"⏳ 正在重试订单 {order_no}...")
    await process_paid_order(order_no, order["user_qq"], order["product_id"])
    await retry_cmd.finish("✅ 重试完成，请查看订单状态")


# === 检查上游付款并发货 ===
check_delivery_cmd = on_command("检查发货", aliases={"checkdeliver"}, permission=SUPERUSER, priority=1, block=True)


@check_delivery_cmd.handle()
async def handle_check_delivery(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """检查上游订单是否已付款并自动发货: /检查发货 <订单号>"""
    order_no = get_command_text(event, args, {"检查发货", "checkdeliver"})
    if not order_no:
        await check_delivery_cmd.finish("❌ 格式: /检查发货 <订单号>")

    await check_delivery_cmd.send(f"⏳ 正在检查订单 {order_no} 的上游付款结果...")

    from ..auto_order import check_manual_payment_order
    result = await check_manual_payment_order(order_no)

    if result.get("success"):
        await check_delivery_cmd.finish(f"✅ 订单 {order_no} 已自动取卡并发货")

    await check_delivery_cmd.finish(
        f"⚠️ 订单 {order_no} 暂未自动发货\n"
        f"原因: {result.get('error', '未知错误')}\n"
        f"如已拿到卡密，请使用: /手动发货 {order_no} 卡密内容"
    )
