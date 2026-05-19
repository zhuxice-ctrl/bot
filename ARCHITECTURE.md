# QQ 自动发卡机器人 - 架构与开发步骤

> **角色**: 代理/分销 — 在 pay.ldxp.cn 有账号余额，用余额自动下单取卡密

> **当前落地模式**: 目标站目前只有支付宝支付，没有余额支付；系统默认采用 `manual_payment` 半自动模式：Bot 自动创建上游支付宝付款单，管理员手动支付，再由 Bot 检查并发货。

## 一、系统总览

```
┌──────────┐  付款   ┌────────────────┐  WS   ┌─────────┐
│ 用户(QQ) │───────▶│  支付宝/微信    │       │ NapCat  │
└──────────┘        └───────┬────────┘       └────┬────┘
                            │ 回调                 │ OneBot v11
                            ▼                      ▼
                    ┌───────────────────────────────────┐
                    │         NoneBot2 (FastAPI)         │
                    │                                   │
                    │  ┌──────────┐  ┌──────────────┐  │
                    │  │ Payment  │  │  QA 客服     │  │
                    │  │ Webhook  │  │ 关键词+AI    │  │
                    │  └────┬─────┘  └──────────────┘  │
                    │       │                           │
                    │       ▼                           │
                    │  ┌──────────────────┐            │
                    │  │  Auto Order      │            │
                    │  │  (Playwright)    │            │
                    │  └────────┬─────────┘            │
                    └───────────┼───────────────────────┘
                                │
                   登录+余额支付 │ 自动操作
                                ▼
                    ┌───────────────────────┐
                    │  pay.ldxp.cn          │
                    │  /shop/RBWM95T3       │
                    │  (Vue3 + Arco Design) │
                    └───────────┬───────────┘
                                │
                                ▼
                        返回卡密内容
                                │
                                ▼
                    QQ 私聊发送给用户
```

## 二、当前核心流程（manual_payment: 自动下单 + 手动上游支付）

```
1. 用户向你付款（支付宝/微信） → 你收到钱
2. 支付平台回调 POST /api/pay/alipay → Bot 创建本地订单
3. Bot 打开 pay.ldxp.cn/shop/RBWM95T3
4. Bot 自动点击商品，填写联系方式、安全密码、数量
5. Bot 点击“去支付”，生成上游支付宝付款链接
6. Bot 私聊管理员：本地订单号、上游订单号、支付宝付款链接
7. 管理员手动完成支付宝付款
8. 管理员发送 `/检查发货 <订单号>`
9. Bot 打开上游订单结果页，尝试提取卡密
10. 成功则自动私聊用户发卡；失败则管理员 `/手动发货 <订单号> <卡密>` 兜底
```

### 原方案 B（余额支付，当前不可用）

```
1. 用户向你付款（支付宝/微信） → 你收到钱
2. 支付平台回调 POST /api/pay/alipay → Bot 知道谁付了多少钱、买什么
3. Bot 用 Playwright 打开 pay.ldxp.cn/shop/RBWM95T3
4. Bot 用预存的 Cookie 登录（首次输入账号密码，之后复用 Cookie）
5. Bot 填写表单：数量=1，联系方式=bot邮箱
6. Bot 选择"余额支付"
7. Bot 点击"立即购买" → 余额直接扣款，无需跳转第三方
8. 页面显示卡密 或 API 返回卡密
9. Bot 提取卡密文本
10. Bot 通过 QQ 私聊发送卡密给用户
```

**关键优势**: 余额支付是站内操作，无需对接支付宝/微信自动付款，Playwright 完全可控。

## 三、目标网站 (pay.ldxp.cn)

| 属性 | 值 |
|------|-----|
| URL | https://pay.ldxp.cn/shop/RBWM95T3 |
| 框架 | Vue 3 SPA |
| UI库 | Arco Design |
| JS入口 | /package/shop/assets/index.eb07c454.js |
| 特征 | 前后端分离，所有数据通过 API 加载 |

### Codex 抓包结论（2026-05-16）

- 公开店铺页已确认接口：`/shopApi/Shop/info`、`/shopApi/Shop/categoryList`、`/shopApi/Shop/goodsList`、`/shopApi/Shop/getGoodsPrice`、`/shopApi/Shop/getUserChannel`、`/shopApi/Pay/order`、`/shopApi/Pay/query`。
- 公开下单弹窗实际选择器见 `docs/api_analysis.md`；提交按钮文案为“去支付”，联系方式 placeholder 为“请输入联系方式方便查询订单”。
- 未登录公开页没有出现“余额支付”。余额扣款 API 必须用有余额账号登录后继续抓包确认。
- 为避免公开 API 创建未支付订单后再回退浏览器，`src/plugins/auto_order/api_client.py` 默认仅在 `.env` 设置 `TARGET_SITE_API_ENABLED=true` 后启用。

### 典型发卡平台购买流程
```
商品页 → 填写(数量+邮箱) → 选支付方式(余额) → 提交 → 扣余额 → 显示卡密
```

### 需要 Codex 确认的选择器（打开 F12 检查）

| 元素 | 猜测选择器 | 需确认 |
|------|-----------|--------|
| 数量框 | `.arco-input-number input` | ✅ |
| 邮箱框 | `input[placeholder*='邮箱']` | ✅ |
| 余额支付 | `.arco-radio:has-text('余额')` | ✅ |
| 购买按钮 | `button:has-text('立即购买')` | ✅ |
| 确认弹窗 | `.arco-modal button:has-text('确认')` | ✅ |
| 卡密区域 | `textarea[readonly]` 或 `.copy-content` | ✅ |
| 登录页URL | `https://pay.ldxp.cn/login` | ✅ |

## 四、开发步骤 (Codex 执行)

---

### Step 1: 抓包确认 API 和选择器 【P0 最高优先级】

**目标**: 确认 pay.ldxp.cn 实际的 DOM 结构和 API

**操作**:
1. Chrome 打开 `https://pay.ldxp.cn/shop/RBWM95T3`
2. F12 → Network → 记录页面加载时的 XHR 请求
3. F12 → Elements → 找到表单元素的实际选择器
4. 登录账号 → 选择余额支付 → 手动下一单
5. 记录整个过程的所有网络请求

**输出**: 更新 `f:\bot\docs\api_analysis.md`，填写：
- 实际的 API 端点和请求/响应格式
- 每个表单元素的精确 CSS 选择器
- 登录接口的请求格式
- 卡密返回的 API 响应结构

---

### Step 2: 根据抓包结果修正 browser.py 选择器 【P0】

**文件**: `f:\bot\src\plugins\auto_order\browser.py`

当前代码已包含通用选择器，Codex 需要：
1. 用 Step 1 确认的精确选择器替换猜测的选择器
2. 确认登录页 URL 是否为 `/login`
3. 确认余额支付的选择方式（radio/button/tab）
4. 确认下单成功后卡密展示方式（同页面/跳转/弹窗）

重点修改的函数：
- `do_login()` — 登录选择器
- `auto_purchase()` Step 2-5 — 表单和按钮选择器
- `extract_card_from_page()` — 卡密提取选择器

---

### Step 3: 编写 API 直调版本（性能优化）【P1】

**文件**: 新建 `f:\bot\src\plugins\auto_order\api_client.py`

抓包后应该能看到前端调用的后端 API，直接用 httpx 调用比 Playwright 快 10x：

```python
"""
pay.ldxp.cn API 直调客户端
基于 Step 1 抓包结果实现
"""
import httpx
from typing import Optional
from nonebot import get_driver
from nonebot.log import logger


class LdxpClient:
    """pay.ldxp.cn 发卡平台 API"""

    BASE = "https://pay.ldxp.cn"

    def __init__(self):
        self.token: str = ""
        self.client = httpx.AsyncClient(timeout=30.0)

    async def login(self) -> bool:
        """登录获取 token/cookie"""
        config = get_driver().config
        # TODO: 根据抓包填写实际登录 API
        resp = await self.client.post(f"{self.BASE}/api/auth/login", json={
            "username": getattr(config, "target_site_username", ""),
            "password": getattr(config, "target_site_password", ""),
        })
        data = resp.json()
        if data.get("code") == 0:
            self.token = data["data"]["token"]
            self.client.headers["Authorization"] = f"Bearer {self.token}"
            return True
        return False

    async def buy_with_balance(self, shop_id: str, quantity: int = 1) -> Optional[str]:
        """余额下单并返回卡密"""
        # TODO: 根据抓包填写实际下单 API
        resp = await self.client.post(f"{self.BASE}/api/order/create", json={
            "shop_id": shop_id,
            "quantity": quantity,
            "pay_type": "balance",
            "contact": "bot@example.com",
        })
        data = resp.json()
        if data.get("code") == 0:
            # 卡密可能直接在响应里，也可能需要再查询
            order_no = data["data"].get("order_no")
            cards = data["data"].get("cards") or data["data"].get("secret")
            if cards:
                return cards if isinstance(cards, str) else "\n".join(cards)
            # 需要额外查询
            return await self.query_card(order_no)
        return None

    async def query_card(self, order_no: str) -> Optional[str]:
        """查询订单卡密"""
        # TODO: 根据抓包填写
        resp = await self.client.get(f"{self.BASE}/api/order/detail", params={"no": order_no})
        data = resp.json()
        if data.get("code") == 0:
            return data["data"].get("card") or data["data"].get("secret")
        return None
```

修改 `auto_order/__init__.py` 中的 `process_paid_order`，优先用 API 直调，失败时 fallback 到 Playwright。

---

### Step 4: 实现支付验签 【P1】

**文件**: `f:\bot\src\plugins\payment\webhook.py`

需要实现真实的验签逻辑，否则任何人都能伪造回调：

**支付宝 RSA2 验签**:
```python
# pip install pycryptodome
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256
import base64

def verify_alipay_sign(params: dict) -> bool:
    sign = params.pop("sign", "")
    params.pop("sign_type", "")
    sorted_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v)
    public_key = RSA.import_key(ALIPAY_PUBLIC_KEY)
    h = SHA256.new(sorted_str.encode("utf-8"))
    try:
        pkcs1_15.new(public_key).verify(h, base64.b64decode(sign))
        return True
    except:
        return False
```

**微信 MD5 验签**:
```python
import hashlib

def verify_wechat_sign(data: dict) -> bool:
    sign = data.pop("sign", "")
    sorted_str = "&".join(f"{k}={v}" for k, v in sorted(data.items()) if v)
    sorted_str += f"&key={WECHAT_API_KEY}"
    expected = hashlib.md5(sorted_str.encode("utf-8")).hexdigest().upper()
    return expected == sign
```

---

### Step 5: 余额监控和自动告警 【P2】

在 `auto_order/__init__.py` 加入余额检查：
- 每次下单前查余额，不足时通知管理员
- 定时任务（每小时）检查余额，低于阈值告警
- 下单失败如果原因是"余额不足"，立即通知管理员充值

---

### Step 6: 端到端测试 【P2】

```bash
# 1. 测试登录
python -c "
import asyncio
from src.plugins.auto_order.browser import get_logged_in_context
asyncio.run(get_logged_in_context())
"

# 2. 测试下单（小心会扣余额！）
python -c "
import asyncio
from src.plugins.auto_order.browser import auto_purchase
result = asyncio.run(auto_purchase('https://pay.ldxp.cn/shop/RBWM95T3', '测试', 'TEST001'))
print(result)
"

# 3. 模拟支付回调
curl -X POST http://localhost:8080/api/pay/alipay \
  -d 'trade_status=TRADE_SUCCESS&out_trade_no=TEST001&total_amount=10&passback_params=123456789|product1'
```

---

## 五、环境配置

### .env 关键配置项
```ini
# 发卡平台账号（有余额的）
TARGET_SITE_URL=https://pay.ldxp.cn
TARGET_SITE_USERNAME=你的账号
TARGET_SITE_PASSWORD=你的密码
CONTACT_EMAIL=接收通知的邮箱

# 你的 QQ Bot
SUPERUSERS=["你的QQ号"]
ADMIN_QQ=你的QQ号

# 支付配置（用户付给你的）
ALIPAY_APP_ID=xxx
ALIPAY_PUBLIC_KEY=xxx
WECHAT_MCH_ID=xxx
WECHAT_API_KEY=xxx
```

### 部署
```bash
pip install -r requirements.txt
playwright install chromium
python bot.py
```

### 公网要求
- 支付回调需要 HTTPS 公网地址
- Nginx 反代 + Let's Encrypt 证书
- 回调: `https://你的域名/api/pay/alipay`

---

## 六、文件结构

```
f:\bot\
├── bot.py                           # 入口
├── .env                             # 配置（账号密钥）
├── requirements.txt                 # 依赖
├── pyproject.toml                   # NoneBot2 配置
├── ARCHITECTURE.md                  # 本文档
├── data/
│   ├── orders.db                    # 订单库（自动创建）
│   ├── keywords.json                # 关键词规则（自动创建）
│   ├── browser_state.json           # 登录Cookie持久化（自动创建）
│   └── screenshots/                 # 调试截图
├── docs/
│   └── api_analysis.md              # 【Codex填写】抓包结果
└── src/plugins/
    ├── database/
    │   ├── __init__.py              # DB 生命周期
    │   └── models.py                # 表结构 + CRUD
    ├── payment/
    │   ├── __init__.py              # 路由注册
    │   └── webhook.py               # 支付回调 + 验签
    ├── auto_order/
    │   ├── __init__.py              # 调度: 收款→下单→发货
    │   ├── browser.py               # Playwright 余额下单（已实现骨架）
    │   └── api_client.py            # 【Codex新建】API直调
    ├── qa/
    │   ├── __init__.py              # 消息路由
    │   ├── keywords.py              # 关键词匹配
    │   └── ai_chat.py              # AI fallback
    └── admin/
        └── __init__.py              # 管理命令
```

---

## 七、Codex 优先级任务表

| 优先级 | 任务 | 文件 | 说明 |
|--------|------|------|------|
| **P0** | 抓包 pay.ldxp.cn | docs/api_analysis.md | F12 Network 记录所有 API |
| **P0** | 修正 browser.py 选择器 | auto_order/browser.py | 用真实选择器替换猜测值 |
| **P1** | 新建 API 直调客户端 | auto_order/api_client.py | httpx 直调，速度优化 |
| **P1** | 实现支付验签 | payment/webhook.py | RSA2 + MD5 真实验签 |
| **P1** | auto_order 加入 API 优先策略 | auto_order/__init__.py | API失败→fallback Playwright |
| **P2** | 余额不足告警 | auto_order/__init__.py | 定时检查+下单前检查 |
| **P2** | 完善错误重试 | auto_order/__init__.py | 指数退避，最多3次 |
| **P3** | 更多关键词 | data/keywords.json | 根据实际用户问题补充 |

---

## 八、注意事项

1. **余额管理**: 确保 pay.ldxp.cn 账户始终有充足余额，建议设置低余额告警
2. **Cookie 过期**: browser_state.json 会自动处理，过期后重新登录
3. **并发控制**: Playwright 同时只跑一个下单任务，避免重复扣款
4. **截图调试**: 每一步都会截图到 data/screenshots/，失败时检查
5. **验签必须实现**: 生产环境不验签 = 任何人可以伪造支付成功
