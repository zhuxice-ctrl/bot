from types import SimpleNamespace

import pytest

import src.plugins.auto_order as auto_order
from src.plugins.auto_order import (
    build_manual_payment_admin_message,
    get_fulfillment_mode,
    normalize_fulfillment_mode,
)
from src.plugins.auto_order.browser import extract_payment_info_from_api


def test_get_fulfillment_mode_defaults_to_manual_payment():
    assert get_fulfillment_mode(SimpleNamespace()) == "manual_payment"


def test_get_fulfillment_mode_accepts_local_stock():
    assert get_fulfillment_mode(SimpleNamespace(order_fulfillment_mode="local_stock")) == "local_stock"


def test_normalize_fulfillment_mode_accepts_chinese_aliases():
    assert normalize_fulfillment_mode("本地") == "local_stock"
    assert normalize_fulfillment_mode("上游") == "manual_payment"
    assert normalize_fulfillment_mode("手动") == "manual"
    assert normalize_fulfillment_mode("自动") == "auto"
    assert normalize_fulfillment_mode("bad") == ""


def test_build_manual_payment_admin_message_contains_payment_link_and_commands():
    product = {"name": "测试商品", "price": 9.9}
    result = {"trade_no": "UPSTREAM001", "pay_url": "https://pay.example/alipay"}

    message = build_manual_payment_admin_message(
        order_no="LOCAL001",
        user_qq="10001",
        product=product,
        result=result,
    )

    assert "LOCAL001" in message
    assert "10001" in message
    assert "测试商品" in message
    assert "https://pay.example/alipay" in message
    assert "/检查发货 LOCAL001" in message
    assert "/手动发货 LOCAL001" in message


def test_extract_payment_info_from_api_finds_trade_no_and_pay_url():
    responses = {
        "https://pay.ldxp.cn/shopApi/Pay/order": {
            "code": 1,
            "data": {
                "trade_no": "T20260516",
                "payurl": "https://alipay.example/pay",
            },
        }
    }

    assert extract_payment_info_from_api(responses) == {
        "trade_no": "T20260516",
        "pay_url": "https://alipay.example/pay",
    }


@pytest.mark.asyncio
async def test_manual_payment_mode_stores_upstream_payment_and_notifies_admin(monkeypatch):
    calls = []
    product = {
        "name": "测试商品",
        "price": 9.9,
        "target_url": "https://pay.ldxp.cn/shop/RBWM95T3",
    }

    class FakeDb:
        async def get_product(self, product_id):
            assert product_id == "prod1"
            return product

        async def update_order_status(self, *args, **kwargs):
            calls.append(("status", args, kwargs))

        async def update_upstream_payment(self, *args, **kwargs):
            calls.append(("upstream", args, kwargs))

    async def fake_create_order_to_payment(*args, **kwargs):
        return {
            "success": True,
            "trade_no": "UPSTREAM001",
            "pay_url": "https://pay.example/alipay",
        }

    async def fake_notify_admin(message):
        calls.append(("notify", message))

    monkeypatch.setattr(auto_order, "db", FakeDb())
    monkeypatch.setattr(auto_order, "create_order_to_payment", fake_create_order_to_payment)
    monkeypatch.setattr(auto_order, "notify_admin", fake_notify_admin)
    monkeypatch.setattr(
        auto_order,
        "get_driver",
        lambda: SimpleNamespace(config=SimpleNamespace(order_fulfillment_mode="manual_payment")),
    )

    await auto_order.process_paid_order("LOCAL001", "10001", "prod1")

    assert ("status", ("LOCAL001", "creating_upstream_payment"), {}) in calls
    assert (
        "upstream",
        ("LOCAL001",),
        {
            "upstream_trade_no": "UPSTREAM001",
            "upstream_pay_url": "https://pay.example/alipay",
            "status": "waiting_upstream_payment",
        },
    ) in calls
    assert any(call[0] == "notify" and "https://pay.example/alipay" in call[1] for call in calls)


@pytest.mark.asyncio
async def test_local_stock_mode_reserves_card_and_delivers(monkeypatch):
    calls = []
    product = {
        "product_id": "prod1",
        "name": "测试商品",
        "price": 9.9,
        "target_url": "local://stock",
    }

    class FakeDb:
        async def get_product(self, product_id):
            assert product_id == "prod1"
            return product

        async def update_order_status(self, *args, **kwargs):
            calls.append(("status", args, kwargs))

        async def reserve_card_stock(self, product_id, order_no):
            calls.append(("reserve", product_id, order_no))
            return "CARD-001"

        async def increment_user_stats(self, user_qq, amount):
            calls.append(("stats", user_qq, amount))

    async def fake_deliver_to_user(*args):
        calls.append(("deliver", args))

    monkeypatch.setattr(auto_order, "db", FakeDb())
    monkeypatch.setattr(auto_order, "deliver_to_user", fake_deliver_to_user)
    monkeypatch.setattr(
        auto_order,
        "get_driver",
        lambda: SimpleNamespace(config=SimpleNamespace(order_fulfillment_mode="local_stock")),
    )

    await auto_order.process_paid_order("LOCAL001", "10001", "prod1")

    assert ("reserve", "prod1", "LOCAL001") in calls
    assert ("status", ("LOCAL001", "processing"), {}) in calls
    assert ("status", ("LOCAL001", "delivered"), {"card_key": "CARD-001"}) in calls
    assert ("stats", "10001", 9.9) in calls
    assert ("deliver", ("10001", "LOCAL001", "测试商品", "CARD-001")) in calls
