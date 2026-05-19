"""
支付宝/微信支付回调路由
"""
from xml.etree import ElementTree

from fastapi import FastAPI, Request, Response
from nonebot import get_driver
from nonebot.log import logger

from ..database import db
from ..auto_order import process_paid_order
from .signing import (
    verify_alipay_sign as verify_alipay_signature,
    verify_wechat_sign as verify_wechat_signature,
)


def setup_routes(app: FastAPI):
    """注册支付回调路由到 FastAPI"""

    @app.post("/api/pay/alipay")
    async def alipay_callback(request: Request):
        """支付宝异步回调"""
        try:
            form_data = await request.form()
            params = dict(form_data)

            # 验签（简化示例，生产环境需要用支付宝公钥验证RSA签名）
            if not verify_alipay_sign(params):
                logger.warning("支付宝回调验签失败")
                return Response(content="fail", media_type="text/plain")

            trade_status = params.get("trade_status", "")
            if trade_status != "TRADE_SUCCESS":
                return Response(content="success", media_type="text/plain")

            # 提取关键信息
            out_trade_no = params.get("out_trade_no", "")  # 商户订单号
            trade_no = params.get("trade_no", "")  # 支付宝交易号
            total_amount = float(params.get("total_amount", 0))
            # passback_params 存放: user_qq|product_id
            passback = params.get("passback_params", "")

            if not passback or "|" not in passback:
                logger.error(f"支付宝回调缺少 passback_params: {out_trade_no}")
                return Response(content="success", media_type="text/plain")

            user_qq, product_id = passback.split("|", 1)

            logger.info(f"支付宝支付成功: 订单={out_trade_no}, 用户={user_qq}, "
                        f"商品={product_id}, 金额={total_amount}")

            # 创建订单记录
            await db.create_order(
                order_no=out_trade_no,
                user_qq=user_qq,
                product_id=product_id,
                amount=total_amount,
                pay_method="alipay",
                pay_trade_no=trade_no
            )

            # 触发自动下单流程
            await process_paid_order(out_trade_no, user_qq, product_id)

            return Response(content="success", media_type="text/plain")

        except Exception as e:
            logger.error(f"支付宝回调处理异常: {e}")
            return Response(content="fail", media_type="text/plain")

    @app.post("/api/pay/wechat")
    async def wechat_callback(request: Request):
        """微信支付异步回调"""
        try:
            body = await request.body()
            data = parse_wechat_xml(body.decode("utf-8"))

            # 验签（简化示例，生产环境需验证微信签名）
            if not verify_wechat_sign(data):
                logger.warning("微信回调验签失败")
                return Response(
                    content="<xml><return_code>FAIL</return_code></xml>",
                    media_type="application/xml"
                )

            result_code = data.get("result_code", "")
            if result_code != "SUCCESS":
                return Response(
                    content="<xml><return_code>SUCCESS</return_code></xml>",
                    media_type="application/xml"
                )

            out_trade_no = data.get("out_trade_no", "")
            transaction_id = data.get("transaction_id", "")
            total_fee = int(data.get("total_fee", 0)) / 100  # 分转元
            # attach 字段存放: user_qq|product_id
            attach = data.get("attach", "")

            if not attach or "|" not in attach:
                logger.error(f"微信回调缺少 attach: {out_trade_no}")
                return Response(
                    content="<xml><return_code>SUCCESS</return_code></xml>",
                    media_type="application/xml"
                )

            user_qq, product_id = attach.split("|", 1)

            logger.info(f"微信支付成功: 订单={out_trade_no}, 用户={user_qq}, "
                        f"商品={product_id}, 金额={total_fee}")

            await db.create_order(
                order_no=out_trade_no,
                user_qq=user_qq,
                product_id=product_id,
                amount=total_fee,
                pay_method="wechat",
                pay_trade_no=transaction_id
            )

            await process_paid_order(out_trade_no, user_qq, product_id)

            return Response(
                content="<xml><return_code>SUCCESS</return_code></xml>",
                media_type="application/xml"
            )

        except Exception as e:
            logger.error(f"微信回调处理异常: {e}")
            return Response(
                content="<xml><return_code>FAIL</return_code></xml>",
                media_type="application/xml"
            )


def verify_alipay_sign(params: dict) -> bool:
    """
    支付宝 RSA/RSA2 验签。
    """
    try:
        public_key = getattr(get_driver().config, "alipay_public_key", "")
    except Exception:
        public_key = ""
    return verify_alipay_signature(params, public_key)


def verify_wechat_sign(data: dict) -> bool:
    """
    微信支付 v2 MD5/HMAC-SHA256 验签。
    """
    try:
        api_key = getattr(get_driver().config, "wechat_api_key", "")
    except Exception:
        api_key = ""
    return verify_wechat_signature(data, api_key)


def parse_wechat_xml(xml_str: str) -> dict:
    """解析微信 XML 回调数据"""
    root = ElementTree.fromstring(xml_str)
    return {child.tag: child.text or "" for child in root}
