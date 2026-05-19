"""pay.ldxp.cn API 客户端。

公开店铺页目前暴露的是买家下单 API。余额扣款相关接口需要登录态和抓包确认，
所以默认只在显式开启后尝试 API 路径，失败时交给 Playwright 回退。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx


CARD_KEYS = {
    "card",
    "cards",
    "secret",
    "secrets",
    "kami",
    "card_key",
    "cardkey",
    "card_info",
    "cardinfo",
    "content",
    "key",
    "keys",
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def extract_shop_token(url_or_token: str) -> Optional[str]:
    """从 `/shop/<token>` URL 中提取店铺 token。"""
    parsed = urlparse(url_or_token)
    path = parsed.path if parsed.scheme else url_or_token
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) >= 2 and parts[0] == "shop":
        return parts[1]
    return None


def extract_goods_key(url_or_key: str) -> Optional[str]:
    """从 `/item/<goods_key>` URL 或裸 key 中提取商品 key。"""
    parsed = urlparse(url_or_key)
    path = parsed.path if parsed.scheme else url_or_key
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) >= 2 and parts[0] == "item":
        return parts[1]
    if len(parts) == 1 and "/" not in url_or_key and "." not in url_or_key:
        return parts[0]
    return None


def _collect_card_values(value: Any, found: list[str], depth: int = 0) -> None:
    if depth > 8:
        return

    if isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower()
            if key_lower in CARD_KEYS:
                _collect_card_values(item, found, depth + 1)
            elif isinstance(item, (dict, list)):
                _collect_card_values(item, found, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _collect_card_values(item, found, depth + 1)
    elif isinstance(value, str):
        text = value.strip()
        if len(text) > 3 and not text.startswith(("http://", "https://")):
            found.append(text)


def extract_card_text(payload: Any) -> Optional[str]:
    """从 API 响应中提取卡密文本。"""
    found: list[str] = []
    _collect_card_values(payload, found)

    unique: list[str] = []
    seen: set[str] = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return "\n".join(unique) if unique else None


def _find_first(payload: Any, keys: set[str], depth: int = 0) -> Optional[Any]:
    if depth > 8:
        return None
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in keys and value not in (None, ""):
                return value
            nested = _find_first(value, keys, depth + 1)
            if nested is not None:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _find_first(item, keys, depth + 1)
            if nested is not None:
                return nested
    return None


@dataclass
class LdxpApiConfig:
    base_url: str = "https://pay.ldxp.cn"
    enabled: bool = True
    contact: str = ""
    query_password: str = ""
    coupon_code: str = ""
    channel_id: int = 0
    pay_type: str = ""
    order_endpoint: str = "/shopApi/Pay/order"
    query_endpoint: str = "/shopApi/Pay/query"
    extra_payload: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0

    @classmethod
    def from_nonebot_config(cls) -> "LdxpApiConfig":
        try:
            from nonebot import get_driver

            config = get_driver().config
        except Exception:
            config = object()

        return cls(
            base_url=str(getattr(config, "target_site_url", "https://pay.ldxp.cn")).rstrip("/"),
            enabled=_as_bool(getattr(config, "target_site_api_enabled", False), default=False),
            contact=str(getattr(config, "contact_email", "") or ""),
            query_password=str(getattr(config, "target_site_query_password", "") or ""),
            coupon_code=str(getattr(config, "coupon_code", "") or ""),
            channel_id=int(getattr(config, "target_site_channel_id", 0) or 0),
            pay_type=str(getattr(config, "target_site_api_pay_type", "") or ""),
            order_endpoint=str(getattr(config, "target_site_order_endpoint", "/shopApi/Pay/order") or "/shopApi/Pay/order"),
            query_endpoint=str(getattr(config, "target_site_query_endpoint", "/shopApi/Pay/query") or "/shopApi/Pay/query"),
        )


class LdxpApiClient:
    def __init__(
        self,
        config: Optional[LdxpApiConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.config = config or LdxpApiConfig.from_nonebot_config()
        self._owns_client = http_client is None
        self.client = http_client or httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Referer": f"{self.config.base_url}/",
            },
        )

    async def __aenter__(self) -> "LdxpApiClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def buy_with_balance(
        self,
        target_url_or_key: str,
        quantity: int = 1,
        contact: str = "",
        query_password: str = "",
        select_cards_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """尝试通过 API 下单并提取卡密。

        返回 `fallback=True` 表示调用方应回退到 Playwright。
        """
        if not self.config.enabled:
            return {"success": False, "fallback": True, "error": "Ldxp API client disabled"}

        goods_key = extract_goods_key(target_url_or_key)
        if not goods_key:
            return {
                "success": False,
                "fallback": True,
                "error": "missing goods_key; use an /item/<goods_key> URL or raw goods key",
            }

        final_contact = contact or self.config.contact
        if not final_contact:
            return {"success": False, "fallback": True, "error": "contact is required for API order"}

        payload: dict[str, Any] = {
            "goods_key": goods_key,
            "quantity": quantity,
            "coupon_code": self.config.coupon_code,
            "channel_id": self.config.channel_id,
            "contact": final_contact,
            "query_password": query_password or self.config.query_password,
            "select_cards_ids": select_cards_ids or [],
            "extend": {},
        }
        if self.config.pay_type:
            payload["pay_type"] = self.config.pay_type
        payload.update(self.config.extra_payload)

        try:
            response = await self.client.post(self.config.order_endpoint, json=payload)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return {"success": False, "fallback": True, "error": f"API order request failed: {exc}"}

        if not self._is_success(data):
            return {
                "success": False,
                "fallback": True,
                "error": str(data.get("msg") or data.get("message") or "API order failed"),
                "response": data,
            }

        trade_no = _find_first(data, {"trade_no", "tradeno", "order_no", "orderno"})
        card_key = extract_card_text(data)
        if card_key:
            return {"success": True, "card_key": card_key, "trade_no": trade_no}

        if trade_no:
            queried = await self.query_card(str(trade_no))
            if queried:
                return {"success": True, "card_key": queried, "trade_no": trade_no}

        return {
            "success": False,
            "fallback": True,
            "error": "API order succeeded but no card was returned",
            "trade_no": trade_no,
            "response": data,
        }

    async def query_card(self, trade_no: str) -> Optional[str]:
        try:
            response = await self.client.post(self.config.query_endpoint, json={"trade_no": trade_no})
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None

        if not self._is_success(data):
            return None
        return extract_card_text(data)

    @staticmethod
    def _is_success(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        if data.get("success") is True:
            return True
        return str(data.get("code")) == "1"
