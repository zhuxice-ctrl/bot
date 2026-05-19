import httpx
import pytest

from src.plugins.auto_order.api_client import (
    LdxpApiClient,
    LdxpApiConfig,
    extract_card_text,
    extract_goods_key,
    extract_shop_token,
)


def test_extract_shop_token_and_goods_key_from_supported_urls():
    assert extract_shop_token("https://pay.ldxp.cn/shop/RBWM95T3") == "RBWM95T3"
    assert extract_shop_token("/shop/RBWM95T3/category") == "RBWM95T3"
    assert extract_goods_key("https://pay.ldxp.cn/item/45h6rd") == "45h6rd"
    assert extract_goods_key("/item/45h6rd/TRADE001") == "45h6rd"
    assert extract_goods_key("45h6rd") == "45h6rd"


def test_extract_card_text_finds_nested_card_fields():
    response = {
        "code": 1,
        "data": {
            "order": {
                "cards": [
                    {"content": "CARD-A"},
                    {"secret": "CARD-B"},
                ]
            }
        },
    }

    assert extract_card_text(response) == "CARD-A\nCARD-B"


@pytest.mark.asyncio
async def test_buy_with_balance_returns_cards_from_order_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/shopApi/Pay/order"
        payload = httpx.Request("POST", request.url, content=request.content).read()
        assert b"goods_key" in payload
        return httpx.Response(
            200,
            json={"code": 1, "data": {"trade_no": "T001", "cards": ["CARD-001"]}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://pay.ldxp.cn") as http_client:
        client = LdxpApiClient(
            LdxpApiConfig(contact="bot@example.com", query_password="safe-pass"),
            http_client=http_client,
        )

        result = await client.buy_with_balance("https://pay.ldxp.cn/item/45h6rd")

    assert result == {"success": True, "card_key": "CARD-001", "trade_no": "T001"}


@pytest.mark.asyncio
async def test_buy_with_balance_returns_retriable_failure_when_goods_key_missing():
    async with httpx.AsyncClient(base_url="https://pay.ldxp.cn") as http_client:
        client = LdxpApiClient(LdxpApiConfig(contact="bot@example.com"), http_client=http_client)

        result = await client.buy_with_balance("https://pay.ldxp.cn/shop/RBWM95T3")

    assert result["success"] is False
    assert result["fallback"] is True
    assert "goods_key" in result["error"]
