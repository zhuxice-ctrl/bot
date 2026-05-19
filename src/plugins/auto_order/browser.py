"""
Playwright 浏览器自动化 - 针对 pay.ldxp.cn 发卡平台余额支付

目标: https://pay.ldxp.cn/shop/RBWM95T3
平台特征: Vue 3 SPA + Arco Design UI
支付方式: 站内余额支付（需要先登录有余额的账号）

使用说明:
1. 首次运行: playwright install chromium
2. 在 .env 中配置 TARGET_SITE_USERNAME / TARGET_SITE_PASSWORD
3. 确保账号有足够余额
4. 首次登录成功后 Cookie 会持久化到 data/browser_state.json
"""
import asyncio
import json
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from nonebot import get_driver
from nonebot.log import logger

# 路径
STATE_FILE = Path(__file__).parent.parent.parent.parent / "data" / "browser_state.json"
SCREENSHOT_DIR = Path(__file__).parent.parent.parent.parent / "data" / "screenshots"

# 全局实例
_playwright = None
_browser: Optional[Browser] = None

# 发卡平台基础 URL
BASE_URL = "https://pay.ldxp.cn"


async def get_browser() -> Browser:
    """获取或创建浏览器实例"""
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
    return _browser


async def get_logged_in_context() -> BrowserContext:
    """
    获取已登录的浏览器上下文
    优先从持久化的 Cookie 恢复会话，避免每次都登录
    """
    browser = await get_browser()

    # 如果有保存的登录状态，尝试恢复
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            context = await browser.new_context(
                storage_state=state,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/125.0.0.0 Safari/537.36"
            )
            # 验证 Cookie 是否仍有效
            page = await context.new_page()
            await page.goto(f"{BASE_URL}/user", wait_until="networkidle", timeout=15000)
            await asyncio.sleep(2)

            # 如果没有跳转到登录页，说明 Cookie 有效
            if "/login" not in page.url and await is_logged_in(page):
                logger.info("Cookie 恢复登录成功")
                await page.close()
                return context
            else:
                logger.info("Cookie 已失效，需要重新登录")
                await page.close()
                await context.close()
        except Exception as e:
            logger.warning(f"恢复 Cookie 失败: {e}")

    # Cookie 无效或不存在，执行新登录
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"
    )

    config = get_driver().config
    username = getattr(config, "target_site_username", "")
    password = getattr(config, "target_site_password", "")

    page = await context.new_page()
    login_ok = await do_login(page, username, password)
    await page.close()

    if login_ok:
        # 持久化登录状态
        await save_state(context)
        return context
    else:
        await context.close()
        raise Exception("登录发卡平台失败，请检查账号密码")


async def save_state(context: BrowserContext):
    """保存浏览器登录状态"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = await context.storage_state()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    logger.info("浏览器登录状态已保存")


async def is_logged_in(page: Page) -> bool:
    """检查是否处于登录状态"""
    try:
        # 发卡平台登录后通常有: 用户头像、余额显示、退出按钮等
        indicators = [
            "text=余额",
            "text=退出",
            "text=个人中心",
            ".user-info",
            ".avatar",
            "[class*='user']",
            "text=充值",
        ]
        for sel in indicators:
            if await page.locator(sel).first.is_visible(timeout=1500):
                return True
    except:
        pass
    return False


async def create_order_to_payment(target_url: str, product_name: str, order_no: str) -> dict:
    """
    自动创建上游订单并停在支付宝支付页。

    只负责打开商品页、填写表单、点击“去支付”、捕获 payurl/trade_no；
    不尝试控制支付宝付款。
    """
    context = None
    page = None
    captured_response = {}

    try:
        browser = await get_browser()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        async def on_response(response):
            url = response.url
            if response.status == 200 and "pay" in url.lower():
                try:
                    captured_response[url] = await response.json()
                    logger.debug(f"[{order_no}] 捕获支付API响应: {url}")
                except Exception:
                    pass

        page.on("response", on_response)

        logger.info(f"[{order_no}] 打开商品页并创建上游付款单: {target_url}")
        await page.goto(target_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_payment_step1.png"))

        if not await ensure_order_form_open(page, product_name, order_no):
            await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_payment_no_form.png"))
            return {"success": False, "error": f"未找到商品或订单确认表单: {product_name}"}

        await fill_order_form(page, order_no)

        submitted = await click_submit_order(page, order_no)
        if not submitted:
            await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_payment_no_submit.png"))
            return {"success": False, "error": "未找到去支付按钮"}

        await asyncio.sleep(3)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_payment_created.png"))

        payment_info = extract_payment_info_from_api(captured_response)
        if payment_info:
            logger.info(f"[{order_no}] 上游付款单创建成功: {payment_info}")
            return {"success": True, **payment_info}

        current_url = page.url if page else ""
        if current_url and "alipay" in current_url.lower():
            return {"success": True, "pay_url": current_url, "trade_no": ""}

        return {"success": False, "error": "已点击去支付，但未捕获到支付宝付款链接或上游订单号"}

    except Exception as e:
        logger.error(f"[{order_no}] 创建上游付款单异常: {e}")
        if page:
            try:
                await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_payment_error.png"))
            except Exception:
                pass
        return {"success": False, "error": str(e)}

    finally:
        if page:
            await page.close()
        if context:
            await context.close()


async def auto_purchase(target_url: str, product_name: str, order_no: str) -> dict:
    """
    自动下单核心逻辑 - 余额支付方案

    完整流程:
    1. 用已登录的会话打开商品页
    2. 填写购买数量(默认1)和联系方式
    3. 选择余额支付
    4. 提交订单
    5. 从结果页或API响应中提取卡密

    Args:
        target_url: 商品页 URL (如 https://pay.ldxp.cn/shop/RBWM95T3)
        product_name: 商品名称
        order_no: 内部订单号

    Returns:
        {"success": True, "card_key": "..."} 或 {"success": False, "error": "..."}
    """
    context = None
    page = None
    captured_response = {}

    try:
        context = await get_logged_in_context()
        page = await context.new_page()

        # 拦截 API 响应，直接从接口获取卡密（比解析 DOM 更可靠）
        async def on_response(response):
            url = response.url
            if response.status == 200:
                # 捕获订单创建/查询相关的 API 响应
                keywords = ["order", "secret", "card", "buy", "purchase", "pay"]
                if any(kw in url.lower() for kw in keywords):
                    try:
                        body = await response.json()
                        captured_response[url] = body
                        logger.debug(f"[{order_no}] 捕获API响应: {url}")
                    except:
                        pass

        page.on("response", on_response)

        # === Step 1: 打开商品页 ===
        logger.info(f"[{order_no}] 打开商品页: {target_url}")
        await page.goto(target_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)  # 等待 Vue 渲染完成

        # 截图记录当前状态（调试用）
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_step1.png"))

        # 如果打开的是店铺页，需要先点击指定商品进入订单确认弹窗
        if not await ensure_order_form_open(page, product_name, order_no):
            await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_no_form.png"))
            return {"success": False, "error": f"未找到商品或订单确认表单: {product_name}"}

        # === Step 2: 填写购买表单 ===
        logger.info(f"[{order_no}] 填写下单表单...")

        # 数量输入框（Arco Design InputNumber）
        qty_input = page.locator(
            ".arco-input-number input, "
            "input[type='number'], "
            "input[placeholder*='数量'], "
            "input[placeholder*='quantity']"
        ).first
        if await qty_input.is_visible(timeout=5000):
            await qty_input.fill("1")

        # 联系方式/邮箱（用于接收订单信息）
        contact_input = page.locator(
            ".confirm_order input[placeholder='请输入联系方式方便查询订单'], "
            "input[placeholder*='邮箱'], "
            "input[placeholder*='email'], "
            "input[placeholder*='联系'], "
            "input[placeholder*='QQ'], "
            "input[type='email'], "
            "input[name='email'], "
            "input[name='contact']"
        ).first
        if await contact_input.is_visible(timeout=3000):
            config = get_driver().config
            # 用管理员邮箱接收
            contact_email = getattr(config, "contact_email", "bot@example.com")
            await contact_input.fill(contact_email)

        # 安全密码/查询密码（链动小铺会用于订单查询和卡密保护）
        security_input = page.locator(
            ".confirm_order input[placeholder*='安全密码'], "
            ".confirm_order input[placeholder*='查询密码'], "
            "input[placeholder*='安全密码'], "
            "input[placeholder*='查询密码']"
        ).first
        if await security_input.is_visible(timeout=2000):
            query_password = getattr(get_driver().config, "target_site_query_password", "")
            await security_input.fill(query_password or order_no[-8:])

        # 优惠码（如果有）
        coupon_input = page.locator(
            "input[placeholder*='优惠'], "
            "input[placeholder*='coupon'], "
            "input[name='coupon']"
        ).first
        if await coupon_input.is_visible(timeout=1000):
            coupon_code = getattr(get_driver().config, "coupon_code", "")
            if coupon_code:
                await coupon_input.fill(coupon_code)

        # === Step 3: 选择余额支付 ===
        logger.info(f"[{order_no}] 选择余额支付...")

        # 发卡平台通常用 radio/tab/button 选择支付方式
        balance_selectors = [
            # 优先限定在订单确认弹窗中，避免点到用户中心的余额文案
            ".confirm_order .arco-radio:has-text('余额')",
            ".confirm_order .arco-radio-button:has-text('余额')",
            ".confirm_order label:has-text('余额')",
            ".confirm_order [data-pay-type='balance']",
            ".confirm_order input[value='balance']",
            ".confirm_order .pay-method:has-text('余额')",
            # 兼容可能不在弹窗内的支付方式组件
            "text=余额支付",
            "text=账户余额",
            ".arco-radio:has-text('余额')",
            ".arco-radio-button:has-text('余额')",
            "label:has-text('余额')",
            "[data-pay-type='balance']",
            "input[value='balance']",
            ".pay-method:has-text('余额')",
        ]

        balance_selected = False
        for sel in balance_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    balance_selected = True
                    logger.info(f"[{order_no}] 已选择余额支付 (selector: {sel})")
                    break
            except:
                continue

        if not balance_selected:
            require_balance = getattr(get_driver().config, "target_site_require_balance", True)
            if str(require_balance).lower() not in {"0", "false", "no", "off"}:
                await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_no_balance.png"))
                return {"success": False, "error": "未找到余额支付选项，已停止以避免走外部支付"}
            logger.warning(f"[{order_no}] 未找到余额支付选项，按配置继续...")

        await asyncio.sleep(1)

        # === Step 4: 提交订单 ===
        logger.info(f"[{order_no}] 提交订单...")

        submit_selectors = [
            ".confirm_order button:has-text('去支付')",
            "button:has-text('立即购买')",
            "button:has-text('提交订单')",
            "button:has-text('确认购买')",
            "button:has-text('购买')",
            "button:has-text('下单')",
            "button:has-text('支付')",
            ".arco-btn-primary:has-text('购买')",
            ".arco-btn-primary:has-text('提交')",
            ".arco-btn-primary:has-text('支付')",
            "button[type='submit']",
        ]

        submitted = False
        for sel in submit_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    submitted = True
                    logger.info(f"[{order_no}] 已点击提交按钮 (selector: {sel})")
                    break
            except:
                continue

        if not submitted:
            await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_no_submit.png"))
            return {"success": False, "error": "未找到提交/购买按钮"}

        # 等待订单处理
        await asyncio.sleep(3)
        await page.wait_for_load_state("networkidle")

        # 可能有二次确认弹窗
        confirm_selectors = [
            ".arco-modal button:has-text('确认')",
            ".arco-modal button:has-text('确定')",
            "button:has-text('确认支付')",
            "button:has-text('确认')",
            ".arco-btn-primary:has-text('确')",
        ]
        for sel in confirm_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    logger.info(f"[{order_no}] 已确认二次弹窗")
                    await asyncio.sleep(2)
                    break
            except:
                continue

        await page.wait_for_load_state("networkidle")
        await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_step4.png"))

        # === Step 5: 提取卡密 ===
        logger.info(f"[{order_no}] 提取卡密...")

        # 方式1: 从拦截的 API 响应中获取
        card_key = extract_card_from_api(captured_response)
        if card_key:
            logger.info(f"[{order_no}] 从API响应获取卡密成功")
            await save_state(context)
            return {"success": True, "card_key": card_key}

        # 方式2: 从页面 DOM 中提取
        card_key = await extract_card_from_page(page)
        if card_key:
            logger.info(f"[{order_no}] 从页面DOM获取卡密成功")
            await save_state(context)
            return {"success": True, "card_key": card_key}

        # 方式3: 检查是否跳转到了订单详情页
        await asyncio.sleep(2)
        card_key = await extract_from_order_detail(page)
        if card_key:
            logger.info(f"[{order_no}] 从订单详情获取卡密成功")
            await save_state(context)
            return {"success": True, "card_key": card_key}

        # 都失败了
        await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_final.png"))
        page_text = await page.content()

        # 检查是否有错误提示
        error_msg = await check_error_message(page)
        if error_msg:
            return {"success": False, "error": error_msg}

        return {"success": False, "error": "下单流程完成但未能提取卡密，请检查截图"}

    except Exception as e:
        logger.error(f"[{order_no}] 浏览器自动化异常: {e}")
        if page:
            try:
                await page.screenshot(path=str(SCREENSHOT_DIR / f"{order_no}_error.png"))
            except:
                pass
        return {"success": False, "error": str(e)}

    finally:
        if page:
            await page.close()
        # 注意: 不关闭 context，复用登录状态


async def ensure_order_form_open(page: Page, product_name: str, order_no: str) -> bool:
    """确保商品购买弹窗已经打开。"""
    if await is_order_form_visible(page):
        return True

    product_name = (product_name or "").strip()
    if not product_name:
        return False

    css_product_name = product_name.replace("\\", "\\\\").replace("'", "\\'")
    selectors = [
        f"text={product_name}",
        f".goods-item:has-text('{css_product_name}')",
        f"[class*='goods']:has-text('{css_product_name}')",
        f"[class*='item']:has-text('{css_product_name}')",
    ]
    for sel in selectors:
        try:
            product = page.locator(sel).first
            if await product.is_visible(timeout=3000):
                await product.click()
                await asyncio.sleep(2)
                if await is_order_form_visible(page):
                    logger.info(f"[{order_no}] 已打开订单确认弹窗 (selector: {sel})")
                    return True
        except Exception:
            continue

    return await is_order_form_visible(page)


async def is_order_form_visible(page: Page) -> bool:
    """判断订单确认弹窗是否可见。"""
    selectors = [
        ".arco-modal.confirm_order",
        ".confirm_order",
        "text=订单确认",
    ]
    for sel in selectors:
        try:
            if await page.locator(sel).first.is_visible(timeout=1000):
                return True
        except Exception:
            continue
    return False


async def fill_order_form(page: Page, order_no: str):
    """填写公开下单弹窗中的通用字段。"""
    qty_input = page.locator(
        ".confirm_order .arco-input-number input, "
        ".arco-input-number input, "
        "input[type='number'], "
        "input[placeholder*='数量'], "
        "input[placeholder*='quantity']"
    ).first
    if await qty_input.is_visible(timeout=5000):
        await qty_input.fill("1")

    contact_input = page.locator(
        ".confirm_order input[placeholder='请输入联系方式方便查询订单'], "
        "input[placeholder*='邮箱'], "
        "input[placeholder*='email'], "
        "input[placeholder*='联系'], "
        "input[placeholder*='QQ'], "
        "input[type='email'], "
        "input[name='email'], "
        "input[name='contact']"
    ).first
    if await contact_input.is_visible(timeout=3000):
        contact_email = getattr(get_driver().config, "contact_email", "bot@example.com")
        await contact_input.fill(contact_email)

    security_input = page.locator(
        ".confirm_order input[placeholder*='安全密码'], "
        ".confirm_order input[placeholder*='查询密码'], "
        "input[placeholder*='安全密码'], "
        "input[placeholder*='查询密码']"
    ).first
    if await security_input.is_visible(timeout=2000):
        query_password = getattr(get_driver().config, "target_site_query_password", "")
        await security_input.fill(query_password or order_no[-8:])

    coupon_input = page.locator(
        "input[placeholder*='优惠'], "
        "input[placeholder*='coupon'], "
        "input[name='coupon']"
    ).first
    if await coupon_input.is_visible(timeout=1000):
        coupon_code = getattr(get_driver().config, "coupon_code", "")
        if coupon_code:
            await coupon_input.fill(coupon_code)


async def click_submit_order(page: Page, order_no: str) -> bool:
    """点击公开下单弹窗中的提交/去支付按钮。"""
    submit_selectors = [
        ".confirm_order button:has-text('去支付')",
        "button:has-text('立即购买')",
        "button:has-text('提交订单')",
        "button:has-text('确认购买')",
        "button:has-text('购买')",
        "button:has-text('下单')",
        "button:has-text('支付')",
        ".arco-btn-primary:has-text('购买')",
        ".arco-btn-primary:has-text('提交')",
        ".arco-btn-primary:has-text('支付')",
        "button[type='submit']",
    ]
    for sel in submit_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                logger.info(f"[{order_no}] 已点击提交按钮 (selector: {sel})")
                return True
        except Exception:
            continue
    return False


def extract_payment_info_from_api(responses: dict) -> Optional[dict]:
    """从支付相关 API 响应中提取支付宝付款链接和上游订单号。"""
    for data in responses.values():
        if not isinstance(data, dict):
            continue
        trade_no = _find_first_value(data, {"trade_no", "tradeno", "order_no", "orderno"})
        pay_url = _find_first_value(data, {"payurl", "pay_url", "paylink", "pay_link", "url"})
        if pay_url and "alipay" not in str(pay_url).lower() and "pay" not in str(pay_url).lower():
            pay_url = None
        if trade_no or pay_url:
            result = {}
            if trade_no:
                result["trade_no"] = str(trade_no)
            if pay_url:
                result["pay_url"] = str(pay_url)
            return result
    return None


def _find_first_value(value, keys: set[str], depth: int = 0):
    if depth > 8:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys and item not in (None, ""):
                return item
            nested = _find_first_value(item, keys, depth + 1)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _find_first_value(item, keys, depth + 1)
            if nested is not None:
                return nested
    return None


def extract_card_from_api(responses: dict) -> Optional[str]:
    """从拦截的 API 响应中提取卡密"""
    for url, data in responses.items():
        if not isinstance(data, dict):
            continue

        # 递归搜索响应体中的卡密字段
        card = _find_card_in_dict(data)
        if card:
            return card

    return None


def _find_card_in_dict(d, depth=0) -> Optional[str]:
    """递归搜索 dict 中可能是卡密的字段"""
    if depth > 5:
        return None

    card_keys = [
        "card", "cards", "secret", "secrets", "kami",
        "card_key", "cardKey", "card_info", "cardInfo",
        "content", "keys"
    ]

    if isinstance(d, dict):
        for k, v in d.items():
            k_lower = k.lower()
            if k_lower in card_keys:
                if isinstance(v, str) and len(v) > 3:
                    return v
                elif isinstance(v, list):
                    # 卡密列表
                    items = [str(item) for item in v if item]
                    if items:
                        return "\n".join(items)
            # 递归
            if isinstance(v, (dict, list)):
                result = _find_card_in_dict(v, depth + 1)
                if result:
                    return result
    elif isinstance(d, list):
        for item in d:
            if isinstance(item, dict):
                result = _find_card_in_dict(item, depth + 1)
                if result:
                    return result
            elif isinstance(item, str) and len(item) > 3:
                # 可能是卡密列表
                pass

    return None


async def extract_card_from_page(page: Page) -> Optional[str]:
    """从页面 DOM 中提取卡密"""
    # 针对 Arco Design + 发卡平台的常见结构
    selectors = [
        # 复制区域（大多数发卡平台都有一键复制）
        "textarea[readonly]",
        "textarea.card-content",
        ".copy-area",
        ".copy-content",
        # 卡密展示
        ".card-key",
        ".card-secret",
        ".kami-content",
        ".order-card",
        "pre",
        "code",
        # Arco Design 组件
        ".arco-textarea-wrapper textarea",
        ".arco-card-body pre",
        # 通用
        "[class*='card'][class*='key']",
        "[class*='secret']",
        "[class*='kami']",
    ]

    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                # 优先取 value（textarea/input）
                value = await el.input_value() if await el.evaluate(
                    "el => el.tagName === 'TEXTAREA' || el.tagName === 'INPUT'"
                ) else None
                if value and value.strip():
                    return value.strip()
                # 否则取 textContent
                text = await el.text_content()
                if text and text.strip() and len(text.strip()) > 3:
                    return text.strip()
        except:
            continue

    # 查找"复制"按钮旁边的内容
    try:
        copy_btn = page.locator(
            "button:has-text('复制'), "
            "button:has-text('一键复制'), "
            "[class*='copy']"
        ).first
        if await copy_btn.is_visible(timeout=2000):
            # 取复制按钮的父级或相邻元素的文本
            parent = copy_btn.locator("xpath=..")
            sibling_text = await parent.locator("textarea, pre, code, span, div").first.text_content()
            if sibling_text and sibling_text.strip():
                return sibling_text.strip()
    except:
        pass

    return None


async def extract_from_order_detail(page: Page) -> Optional[str]:
    """从订单详情页提取卡密"""
    try:
        # 检查当前 URL 是否已经是订单页面
        current_url = page.url
        if "order" in current_url.lower():
            return await extract_card_from_page(page)

        # 尝试跳转到订单查看页面
        order_link_selectors = [
            "a:has-text('查看订单')",
            "a:has-text('订单详情')",
            "a:has-text('查看卡密')",
            "button:has-text('查看')",
            "a[href*='order']",
        ]
        for sel in order_link_selectors:
            try:
                link = page.locator(sel).first
                if await link.is_visible(timeout=2000):
                    await link.click()
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(2)
                    return await extract_card_from_page(page)
            except:
                continue
    except:
        pass

    return None


async def query_paid_order_card(upstream_trade_no: str, order_no: str = "") -> dict:
    """付款完成后访问上游订单详情页并尝试提取卡密。"""
    context = None
    page = None
    captured_response = {}
    trace_id = order_no or upstream_trade_no

    try:
        browser = await get_browser()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        async def on_response(response):
            url = response.url
            if response.status == 200 and any(key in url.lower() for key in ("order", "card", "pay")):
                try:
                    captured_response[url] = await response.json()
                except Exception:
                    pass

        page.on("response", on_response)

        await page.goto(f"{BASE_URL}/order/result/{upstream_trade_no}", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        query_password = getattr(get_driver().config, "target_site_query_password", "")
        if query_password:
            password_input = page.locator(
                "input[placeholder*='安全密码'], "
                "input[placeholder*='查询密码'], "
                "input[placeholder*='密码']"
            ).first
            if await password_input.is_visible(timeout=1500):
                await password_input.fill(query_password)
                for sel in ("button:has-text('查询')", "button:has-text('查看')", "button:has-text('确认')"):
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            await asyncio.sleep(2)
                            break
                    except Exception:
                        continue

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(SCREENSHOT_DIR / f"{trace_id}_query_result.png"))

        card_key = extract_card_from_api(captured_response)
        if card_key:
            return {"success": True, "card_key": card_key}

        card_key = await extract_card_from_page(page)
        if card_key:
            return {"success": True, "card_key": card_key}

        error_msg = await check_error_message(page)
        if error_msg:
            return {"success": False, "error": error_msg}

        return {"success": False, "error": "未找到卡密，可能还未完成支付宝付款"}

    except Exception as e:
        logger.error(f"[{trace_id}] 查询上游订单异常: {e}")
        return {"success": False, "error": str(e)}

    finally:
        if page:
            await page.close()
        if context:
            await context.close()


async def check_error_message(page: Page) -> Optional[str]:
    """检查页面上是否有错误提示"""
    error_selectors = [
        ".arco-message-error",
        ".arco-notification-error",
        ".arco-alert-error",
        ".error-message",
        "text=余额不足",
        "text=库存不足",
        "text=商品已下架",
        "text=操作失败",
        "text=失败",
    ]
    for sel in error_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1000):
                text = await el.text_content()
                return text.strip() if text else "未知错误"
        except:
            continue
    return None


async def do_login(page: Page, username: str, password: str) -> bool:
    """登录 pay.ldxp.cn"""
    try:
        # 跳转到登录页
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=20000)
        await asyncio.sleep(2)

        # 填写用户名/邮箱（Arco Input）
        username_input = page.locator(
            "input[placeholder*='用户名'], "
            "input[placeholder*='邮箱'], "
            "input[placeholder*='账号'], "
            "input[type='text'].arco-input, "
            "input[name='username'], "
            "input[name='email'], "
            ".arco-input-wrapper input[type='text']"
        ).first
        await username_input.click()
        await username_input.fill(username)

        # 填写密码
        password_input = page.locator(
            "input[type='password'], "
            "input[placeholder*='密码'], "
            "input[name='password'], "
            ".arco-input-wrapper input[type='password']"
        ).first
        await password_input.click()
        await password_input.fill(password)

        await asyncio.sleep(0.5)

        # 点击登录
        login_btn = page.locator(
            "button:has-text('登录'), "
            "button:has-text('Login'), "
            "button[type='submit'], "
            ".arco-btn-primary"
        ).first
        await login_btn.click()

        # 等待跳转
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(3)

        # 验证登录结果
        if "/login" not in page.url:
            logger.info(f"登录成功，当前页面: {page.url}")
            return True

        # 检查错误信息
        error = await check_error_message(page)
        if error:
            logger.error(f"登录失败: {error}")
        else:
            logger.error("登录失败: 仍在登录页面")
        return False

    except Exception as e:
        logger.error(f"登录异常: {e}")
        return False


async def check_balance(page: Page) -> Optional[float]:
    """查询当前账户余额"""
    try:
        await page.goto(f"{BASE_URL}/user", wait_until="networkidle", timeout=15000)
        await asyncio.sleep(2)

        # 查找余额显示
        balance_el = page.locator(
            "text=/\\d+\\.\\d{2}/, "
            "[class*='balance'], "
            ":has-text('余额') >> xpath=following-sibling::*"
        ).first

        if await balance_el.is_visible(timeout=3000):
            text = await balance_el.text_content()
            import re
            match = re.search(r"(\d+\.?\d*)", text)
            if match:
                return float(match.group(1))
    except:
        pass
    return None
