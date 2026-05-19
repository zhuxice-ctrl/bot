import base64
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.plugins.payment.signing import (
    build_alipay_sign_content,
    build_wechat_sign_content,
    verify_alipay_sign,
    verify_wechat_sign,
)


def test_alipay_rsa2_verify_accepts_valid_signature_without_mutating_params():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    params = {
        "trade_no": "2026051622000000001",
        "out_trade_no": "ORDER001",
        "total_amount": "10.00",
        "trade_status": "TRADE_SUCCESS",
        "sign_type": "RSA2",
    }
    content = build_alipay_sign_content(params)
    signature = private_key.sign(
        content.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signed_params = {**params, "sign": base64.b64encode(signature).decode("ascii")}
    original = dict(signed_params)

    assert verify_alipay_sign(signed_params, public_key_pem) is True
    assert signed_params == original


def test_alipay_rsa2_verify_rejects_tampered_payload():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    params = {
        "trade_no": "2026051622000000001",
        "out_trade_no": "ORDER001",
        "total_amount": "10.00",
        "trade_status": "TRADE_SUCCESS",
        "sign_type": "RSA2",
    }
    signature = private_key.sign(
        build_alipay_sign_content(params).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    params["sign"] = base64.b64encode(signature).decode("ascii")
    params["total_amount"] = "1000.00"

    assert verify_alipay_sign(params, public_key_pem) is False


def test_wechat_md5_verify_accepts_valid_signature_without_mutating_data():
    api_key = "secret-api-key"
    data = {
        "appid": "wx123",
        "mch_id": "mch123",
        "out_trade_no": "ORDER001",
        "result_code": "SUCCESS",
        "total_fee": "1000",
    }
    content = build_wechat_sign_content(data, api_key)
    signed_data = {**data, "sign": hashlib.md5(content.encode("utf-8")).hexdigest().upper()}
    original = dict(signed_data)

    assert verify_wechat_sign(signed_data, api_key) is True
    assert signed_data == original


def test_wechat_hmac_sha256_verify_accepts_valid_signature():
    api_key = "secret-api-key"
    data = {
        "appid": "wx123",
        "mch_id": "mch123",
        "out_trade_no": "ORDER001",
        "result_code": "SUCCESS",
        "sign_type": "HMAC-SHA256",
        "total_fee": "1000",
    }
    content = build_wechat_sign_content(data, api_key)
    signed_data = {
        **data,
        "sign": hmac.new(api_key.encode("utf-8"), content.encode("utf-8"), hashlib.sha256)
        .hexdigest()
        .upper(),
    }

    assert verify_wechat_sign(signed_data, api_key) is True
