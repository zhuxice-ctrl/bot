import aiosqlite
import pytest

from src.plugins.database.models import Database


@pytest.mark.asyncio
async def test_update_upstream_payment_persists_trade_no_and_pay_url():
    database = Database()
    database.db = await aiosqlite.connect(":memory:")
    database.db.row_factory = aiosqlite.Row
    try:
        await database._create_tables()
        await database.create_order(
            order_no="LOCAL001",
            user_qq="10001",
            product_id="prod1",
            amount=9.9,
            pay_method="alipay",
            pay_trade_no="PAY001",
        )

        await database.update_upstream_payment(
            "LOCAL001",
            upstream_trade_no="UPSTREAM001",
            upstream_pay_url="https://pay.example/alipay",
            status="waiting_upstream_payment",
        )

        order = await database.get_order("LOCAL001")
    finally:
        await database.close()

    assert order["status"] == "waiting_upstream_payment"
    assert order["upstream_trade_no"] == "UPSTREAM001"
    assert order["upstream_pay_url"] == "https://pay.example/alipay"


@pytest.mark.asyncio
async def test_card_stock_reserve_marks_first_available_card_sold():
    database = Database()
    database.db = await aiosqlite.connect(":memory:")
    database.db.row_factory = aiosqlite.Row
    try:
        await database._create_tables()
        await database.add_product("prod1", "测试商品", 9.9, "local://stock")
        inserted = await database.add_card_stock("prod1", ["CARD-A", "CARD-B", "CARD-A"])

        card = await database.reserve_card_stock("prod1", "ORDER001")
        remaining = await database.get_stock_count("prod1")
        counts = await database.get_all_stock_counts()
    finally:
        await database.close()

    assert inserted == 2
    assert card == "CARD-A"
    assert remaining == 1
    assert counts == [{"product_id": "prod1", "name": "测试商品", "stock": 1}]


@pytest.mark.asyncio
async def test_app_settings_persist_runtime_mode():
    database = Database()
    database.db = await aiosqlite.connect(":memory:")
    database.db.row_factory = aiosqlite.Row
    try:
        await database._create_tables()
        await database.set_setting("order_fulfillment_mode", "local_stock")
        first = await database.get_setting("order_fulfillment_mode")
        await database.set_setting("order_fulfillment_mode", "manual_payment")
        second = await database.get_setting("order_fulfillment_mode")
    finally:
        await database.close()

    assert first == "local_stock"
    assert second == "manual_payment"
