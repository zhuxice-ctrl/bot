# QQ 自动发卡机器人

QQ 自动发卡机器人：检测用户付款 → 本地库存自动发卡 / 上游半自动下单 → QQ 私聊发货 → 智能客服

## 架构

```
用户付款给你 → 支付宝/微信回调 → Bot 接收通知
    ↓
本地卡密库存充足时直接扣库存发货
    ↓
也可切换到上游模式：自动创建支付宝付款页 → 管理员付款 → /检查发货
    ↓
提取卡密或手动发货 → 通过 QQ 私聊发送给用户
    ↓
用户提问 → 关键词匹配 / AI 大模型回复
```

## 技术栈

- **QQ 协议**: NapCat (OneBot v11)
- **Bot 框架**: NoneBot2
- **支付接收**: FastAPI (支付宝/微信回调)
- **上游下单**: Playwright (自动填单到支付宝付款页)
- **数据存储**: SQLite (aiosqlite)
- **智能客服**: OpenAI API (兼容通义千问/DeepSeek)
- **视频报告**: yt-dlp + DeepSeek，语音转写可选 faster-whisper

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置环境变量

编辑 `.env` 文件，填写：
- QQ Bot 连接信息
- 支付宝/微信商户密钥
- `ORDER_FULFILLMENT_MODE=local_stock`
- 上游订单联系方式、查询安全密码
- OpenAI API Key（可选）
- 视频报告路径配置（可选）

### 3. 启动 NapCat

参考 [NapCat 文档](https://napcat.napneko.icu/) 启动并配置 WebSocket 连接。

连接地址设置为: `ws://127.0.0.1:3011`（与 .env 中 ONEBOT_WS_URLS 一致）

### 4. 启动机器人

```bash
python bot.py
```

## 使用指南

### 管理员命令 (需要 SUPERUSER 权限)

| 命令 | 说明 |
|------|------|
| `/切换 [本地/上游/手动/自动]` | 查看或切换发货模式，仅管理员可用 |
| `/录入帮助` | 查看商品、价格、卡密导入格式 |
| `/添加商品 <ID> <名称> <价格> <URL> [描述]` | 添加商品 |
| `/添加本地商品 <ID> <名称> <价格> [描述]` | 添加本地库存商品 |
| `/导入卡密 <商品ID>` 换行后粘贴卡密 | 批量导入本地卡密，一行一个 |
| `/库存 [商品ID]` | 查看本地卡密库存 |
| `/手动发货 <订单号> <卡密>` | 手动发货 |
| `/检查发货 <订单号>` | 管理员完成上游支付宝付款后，检查上游订单并尝试自动取卡发货 |
| `/统计` | 查看系统统计 |
| `/重试 <订单号>` | 重试失败订单 |

### 用户命令

| 命令 | 说明 |
|------|------|
| `/帮助` | 查看帮助菜单 |
| `/商品` | 查看商品列表 |
| `/订单` | 查看最近订单 |
| `/订单 <订单号>` | 查询订单详情 |
| `/视频报告 <抖音链接>` | 下载/读取短视频信息并生成报告 |
| `/tk [抖音链接]` | 切换到视频报告模式；可直接发送抖音分享文本 |
| `/shop` | 切回商品/发货模式 |
| `/回顾` | 查看上一份尚未细致追问的视频报告检索信息 |

直接私聊发送抖音链接也会触发视频报告；群里默认需要 @ 机器人。切换到 `/tk` 模式后，群里直接发送抖音分享文本也会触发视频报告。

## 视频报告工作流

发卡支付功能先保留在项目中，新的视频报告功能独立在 `src/plugins/video_report/`。

推荐先本地克隆 yt-dlp：

```powershell
git clone --depth 1 https://github.com/yt-dlp/yt-dlp.git F:\bot\vendor\yt-dlp
```

当前网络如果无法访问 GitHub，程序会回退使用 Python 环境中已安装的 `yt_dlp` 模块。

基础流程：

```text
用户发送抖音链接
↓
yt-dlp 读取标题、作者、描述、时长等元数据
↓
如果安装了 faster-whisper，临时下载音频并转写
↓
DeepSeek 根据元数据和语音转写生成中文报告
↓
临时媒体文件默认删除
```

相关配置：

```ini
VIDEO_REPORT_YTDLP_REPO=F:\bot\vendor\yt-dlp
VIDEO_REPORT_WORK_DIR=F:\bot\data\video_reports
VIDEO_REPORT_COOKIE_FILE=F:\bot\data\cookies\douyin.txt
VIDEO_REPORT_BROWSER_RESOLVE=true
VIDEO_REPORT_BROWSER_HEADLESS=true
VIDEO_REPORT_BROWSER_TIMEOUT_MS=45000
VIDEO_REPORT_BROWSER_EXECUTABLE_PATH=
VIDEO_REPORT_DOWNLOAD_MEDIA=true
VIDEO_REPORT_KEEP_MEDIA=false
VIDEO_REPORT_WHISPER_MODEL=base
VIDEO_REPORT_MAX_CONCURRENCY=1
```

抖音如果提示 `Fresh cookies are needed`，需要从已登录抖音的浏览器导出 Netscape 格式 cookies，并把文件路径填到 `VIDEO_REPORT_COOKIE_FILE`。服务器部署时建议放在 `/opt/qq-video-bot/data/cookies/douyin.txt`。
开启 `VIDEO_REPORT_BROWSER_RESOLVE` 后，程序会先用服务器浏览器打开抖音页面并缓存详情 JSON，再把播放直链交给下载/转写流程。
连续发送多个链接时，机器人会同时接收消息，并按 `VIDEO_REPORT_MAX_CONCURRENCY` 限制生成任务数量。服务器 CPU 较弱时建议保持 `1`，避免多个转写任务同时跑满 CPU。

需要语音转写时安装可选依赖：

```bash
pip install faster-whisper
```

### 支付回调接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/pay/alipay` | POST | 支付宝异步通知 |
| `/api/pay/wechat` | POST | 微信支付异步通知 |

## 履约模式

默认模式是本地库存发货：

```ini
ORDER_FULFILLMENT_MODE=local_stock
```

流程：

1. 用户付款给你，支付回调创建本地订单。
2. Bot 从 `card_stock` 本地库存中锁定一张可用卡密。
3. Bot 更新订单为已发货，并通过 QQ 私聊发送卡密给用户。
4. 如果库存不足，订单标记异常并通知管理员导入卡密后 `/重试 <订单号>`。

可选模式：

| 模式 | 说明 |
|------|------|
| `local_stock` | 推荐。提前买好卡密导入本地，支付成功后直接发货 |
| `manual_payment` | 自动生成上游付款页，管理员只负责支付宝付款 |
| `manual` | 用户付款后只通知管理员，采购和发货都手动 |
| `auto` | 全自动余额支付；当前目标站只有支付宝，不建议启用 |

本地库存商品示例：

```text
/切换 本地
/添加本地商品 vip30 月卡VIP 30 一个月VIP会员
/导入卡密 vip30
CARD-001
CARD-002
/库存 vip30
```

切回上游半自动模式：

```text
/切换 上游
```

## 自定义下单逻辑

核心文件: `src/plugins/auto_order/browser.py`

你需要根据目标网站的实际结构修改：
1. `create_order_to_payment()` - 自动创建上游支付宝付款单
2. `query_paid_order_card()` - 付款后查询上游订单并提取卡密
3. `auto_purchase()` - 仅用于 `auto` 余额支付模式

### 调试技巧

- 设置 `headless=False` 可以看到浏览器操作过程
- 失败订单会自动截图到 `data/screenshots/`
- 使用 `/重试 <订单号>` 重新执行失败订单

## 自定义关键词回复

编辑 `data/keywords.json`：

```json
[
  {
    "patterns": ["关键词1", "关键词2"],
    "reply": "回复内容",
    "mode": "contain"
  }
]
```

mode 支持: `contain`(包含), `exact`(精确), `regex`(正则)

## 目录结构

```
f:\bot\
├── bot.py                       # 入口
├── .env                         # 配置
├── requirements.txt             # 依赖
├── pyproject.toml               # NoneBot2 配置
├── data/
│   ├── orders.db                # SQLite 数据库(自动创建)
│   ├── keywords.json            # 关键词规则(自动创建)
│   └── screenshots/             # 错误截图
└── src/plugins/
    ├── database/                # 数据库模块
    │   ├── __init__.py
    │   └── models.py
    ├── payment/                 # 支付回调
    │   ├── __init__.py
    │   └── webhook.py
    ├── auto_order/              # 自动下单
    │   ├── __init__.py
    │   └── browser.py           # ← 需要根据目标网站定制
    ├── qa/                      # 问答客服
    │   ├── __init__.py
    │   ├── keywords.py
    │   └── ai_chat.py
    └── admin/                   # 管理员命令
        └── __init__.py
```

## 注意事项

1. **支付验签**: 已实现支付宝 RSA/RSA2、微信 MD5/HMAC-SHA256 验签，请确保 `.env` 中密钥正确
2. **浏览器自动化**: 需要根据目标网站实际结构调整选择器
3. **安全**: `.env` 文件包含敏感信息，切勿提交到 Git
4. **稳定性**: 建议使用 supervisord/systemd 管理进程，确保异常重启
