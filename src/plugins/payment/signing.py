"""支付平台回调签名验证。"""
import base64
import hashlib
import hmac
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _signable_items(data: Mapping[str, object], excluded: set[str]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in data.items():
        if key in excluded or value is None or value == "":
            continue
        items.append((key, str(value)))
    return sorted(items, key=lambda item: item[0])


def build_alipay_sign_content(params: Mapping[str, object]) -> str:
    """按支付宝异步通知规则构造待验签字符串。"""
    return "&".join(
        f"{key}={value}"
        for key, value in _signable_items(params, {"sign", "sign_type"})
    )


def _normalize_public_key(public_key: str) -> bytes:
    key = public_key.strip().replace("\\n", "\n")
    if "BEGIN PUBLIC KEY" not in key:
        key = f"-----BEGIN PUBLIC KEY-----\n{key}\n-----END PUBLIC KEY-----"
    return key.encode("utf-8")


def verify_alipay_sign(params: Mapping[str, object], public_key: str) -> bool:
    """验证支付宝 RSA2 签名。"""
    sign = str(params.get("sign", "") or "")
    if not sign or not public_key:
        return False

    sign_type = str(params.get("sign_type", "RSA2") or "RSA2").upper()
    hash_algorithm = hashes.SHA256() if sign_type == "RSA2" else hashes.SHA1()

    try:
        key = serialization.load_pem_public_key(_normalize_public_key(public_key))
        key.verify(
            base64.b64decode(sign),
            build_alipay_sign_content(params).encode("utf-8"),
            padding.PKCS1v15(),
            hash_algorithm,
        )
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False


def build_wechat_sign_content(data: Mapping[str, object], api_key: str) -> str:
    """按微信支付 v2 规则构造待签名字符串。"""
    content = "&".join(
        f"{key}={value}"
        for key, value in _signable_items(data, {"sign"})
    )
    return f"{content}&key={api_key}"


def verify_wechat_sign(data: Mapping[str, object], api_key: str) -> bool:
    """验证微信支付 v2 MD5/HMAC-SHA256 签名。"""
    sign = str(data.get("sign", "") or "").upper()
    if not sign or not api_key:
        return False

    content = build_wechat_sign_content(data, api_key)
    sign_type = str(data.get("sign_type", "MD5") or "MD5").upper()
    if sign_type == "HMAC-SHA256":
        expected = hmac.new(
            api_key.encode("utf-8"),
            content.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()
    else:
        expected = hashlib.md5(content.encode("utf-8")).hexdigest().upper()

    return hmac.compare_digest(expected, sign)
