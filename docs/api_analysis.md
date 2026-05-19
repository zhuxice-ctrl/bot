# pay.ldxp.cn API 抓包分析

分析时间: 2026-05-16
目标页: `https://pay.ldxp.cn/shop/RBWM95T3`

## 结论

- 公开店铺页是 Vue 3 + Arco Design，主入口为 `/package/shop/assets/index.eb07c454.js`。
- 商品购买弹窗来自 `/package/shop/assets/index.950a5437.js`。
- 公开买家下单 API 已确认，但它返回的是外部支付 `payurl` 或未支付订单状态，不等同于“站内余额扣款”。
- 未登录公开页面没有显示“余额支付”选项；余额扣款接口仍需用有余额账号登录后抓包确认。
- 直接用普通 HTTP 客户端请求 API 会遇到 `acw_sc__v2` 反爬挑战，生产中仍需要 Playwright Cookie/登录态作为兜底。

## 已确认的前端资源

| 类型 | URL |
|------|-----|
| 主入口 | `/package/shop/assets/index.eb07c454.js` |
| 店铺/下单组件 | `/package/shop/assets/index.950a5437.js` |
| 选卡插件 | `/package/shop/assets/select-cards.4cb9c683.js` |
| 订单结果布局 | `/package/shop/assets/order-layout.a13805b5.js` |
| 插件脚本 | `/plugin/Ordercardsend/api/js` |
| 风控脚本 | `/shopApi/Shop/buyerBlackJs?token=RBWM95T3` |

## 公开店铺 API

### 1. 店铺信息

- **URL**: `POST /shopApi/Shop/info`
- **Body**:

```json
{
  "token": "RBWM95T3",
  "category_key": null
}
```

- **用途**: 获取店铺信息、店铺样式、插件 JS。
- **成功码**: 前端判断 `code === 1` 为成功。

### 2. 分类列表

- **URL**: `POST /shopApi/Shop/categoryList`
- **Body**:

```json
{
  "token": "RBWM95T3",
  "goods_type": "card",
  "category_key": null
}
```

### 3. 商品列表

- **URL**: `POST /shopApi/Shop/goodsList`
- **Body**:

```json
{
  "token": "RBWM95T3",
  "keywords": "",
  "category_id": -1,
  "goods_type": "card",
  "current": 1,
  "pageSize": 20
}
```

### 4. 支付/发货通道

- **URL**: `POST /shopApi/Shop/getUserChannel`
- **Body**:

```json
{
  "token": "RBWM95T3"
}
```

### 5. 商品价格

- **URL**: `POST /shopApi/Shop/getGoodsPrice`
- **Body**:

```json
{
  "goods_key": "45h6rd",
  "quantity": 1,
  "coupon_code": "",
  "channel_id": 0
}
```

### 6. 创建公开买家订单

- **URL**: `POST /shopApi/Pay/order`
- **Body**:

```json
{
  "goods_key": "45h6rd",
  "quantity": 1,
  "coupon_code": "",
  "channel_id": 0,
  "contact": "bot@example.com",
  "query_password": "order-query-password",
  "select_cards_ids": [],
  "extend": {}
}
```

- **前端逻辑**:
  - `code === 0`: 显示错误消息。
  - `data.total_amount === 0`: 跳转 `/order/result/{trade_no}`。
  - 其他情况: 打开 `data.payurl`，并轮询 `/shopApi/Pay/query`。

### 7. 查询支付结果

- **URL**: `POST /shopApi/Pay/query`
- **Body**:

```json
{
  "trade_no": "平台订单号"
}
```

- **前端逻辑**: `code === 1` 后跳转 `/order/result/{trade_no}`。

### 8. 选卡插件

- **URL**:
  - `POST /shopApi/Shop/selectCardsPre`
  - `POST /shopApi/Shop/selectCards`
- **用途**: 部分商品允许买家预选卡密。当前目标商品弹窗中出现“选择账号”插件弹窗，但列表为空。

## 页面 DOM 选择器记录

公开页面打开有库存商品“Windsurf 试用号（谷歌母号）”后，实际下单表单在 Arco Modal 中。

| 元素 | 选择器 | 备注 |
|------|--------|------|
| 订单确认弹窗 | `.arco-modal.confirm_order` | 实际 modal class |
| 商品入口 | `text=<商品名>` | 店铺页先点商品名打开弹窗 |
| 商品直达链接 | `/item/45h6rd` | 弹窗内可见商品链接 |
| 联系方式 | `.confirm_order input[placeholder='请输入联系方式方便查询订单']` | 公开页实际 placeholder |
| 安全密码 | `.confirm_order input[placeholder='为保障您的卡密安全，请设置安全密码']` | 用于订单查询/卡密保护 |
| 数量输入框 | `.confirm_order .arco-input-number input` | 当前值默认 `1` |
| 优惠券勾选 | `.confirm_order input[type='checkbox'].arco-checkbox-target` | 可选 |
| 提交按钮 | `.confirm_order button:has-text('去支付')` | 真实文案不是“立即购买” |
| 取消按钮 | `.confirm_order button:has-text('取消')` | |
| 余额支付 | 未在公开页出现 | 需登录有余额账号后确认 |
| 卡密显示区 | 未确认 | 需完成支付或有已支付订单后确认 |

## 待登录态确认

这些信息无法在不登录、不创建真实扣款订单的前提下确认：

- 登录页 URL 和账号密码表单选择器。
- 站内余额支付选项是否在订单弹窗、支付页、还是用户中心内出现。
- 余额支付实际请求体，尤其是是否存在 `pay_type=balance`、余额通道 `channel_id`、CSRF token。
- 余额扣款成功后，卡密是直接在 `/shopApi/Pay/order` 返回，还是需要访问 `/order/result/{trade_no}`。
- 订单结果页卡密字段结构。

## 当前代码映射

- `src/plugins/auto_order/browser.py`
  - 已按公开 DOM 增加 `.confirm_order`、联系方式、安全密码、数量、`去支付` 选择器。
  - 默认要求找到“余额”支付方式；找不到会停止，避免误走外部支付。
  - 已新增 `create_order_to_payment()`：只创建上游支付宝付款单，不执行支付宝付款。
  - 已新增 `query_paid_order_card()`：管理员付款后通过 `/检查发货 <订单号>` 查询上游结果页并尝试取卡。
- `src/plugins/auto_order/__init__.py`
  - 默认 `ORDER_FULFILLMENT_MODE=manual_payment`。
  - 本地订单付款成功后，Bot 生成上游付款链接并通知管理员；管理员付款后再检查发货。
- `src/plugins/auto_order/api_client.py`
  - 已实现可配置 API 客户端。
  - `.env` 未显式设置 `TARGET_SITE_API_ENABLED=true` 时不会自动调用公开下单接口，避免制造未支付订单。
- `src/plugins/payment/webhook.py`
  - 已接入真实支付宝 RSA/RSA2、微信 MD5/HMAC-SHA256 验签。
