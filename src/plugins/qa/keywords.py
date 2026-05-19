"""
关键词自动回复
从 data/keywords.json 加载规则
"""
import json
import re
from pathlib import Path
from typing import Optional
from nonebot.log import logger

KEYWORDS_FILE = Path(__file__).parent.parent.parent.parent / "data" / "keywords.json"

# 缓存
_keywords_cache: list = []
_cache_mtime: float = 0


def load_keywords() -> list:
    """
    加载关键词规则
    格式: [{"patterns": ["关键词1", "关键词2"], "reply": "回复内容", "mode": "contain|exact|regex"}]
    """
    global _keywords_cache, _cache_mtime

    if not KEYWORDS_FILE.exists():
        # 创建默认规则
        default_rules = [
            {
                "patterns": ["怎么买", "如何购买", "怎么下单"],
                "reply": "💡 购买流程:\n1. 发送 /商品 查看商品和价格\n2. 确认商品后按店主/页面提示付款\n3. 支付成功后系统会通过QQ私聊自动发货\n\n如有问题请提供订单号联系管理员",
                "mode": "contain"
            },
            {
                "patterns": ["多久发货", "什么时候发", "发货时间"],
                "reply": "⏰ 支付成功后通常 1-3 分钟内自动发货\n如超过 5 分钟未收到，请发送 /订单 查看状态",
                "mode": "contain"
            },
            {
                "patterns": ["卡密无效", "不能用", "用不了", "无法使用"],
                "reply": "😥 如果卡密无法使用，请:\n1. 确认是否正确复制（无多余空格）\n2. 确认是否已被使用过\n3. 如确认有问题，请联系管理员并提供订单号",
                "mode": "contain"
            },
            {
                "patterns": ["退款", "退钱"],
                "reply": "💰 关于退款:\n虚拟商品一经发货不支持退款\n如确有质量问题，请联系管理员处理",
                "mode": "contain"
            },
            {
                "patterns": ["客服", "人工", "管理员"],
                "reply": "👤 如需人工客服，请直接描述您的问题，管理员会尽快回复",
                "mode": "contain"
            }
        ]
        KEYWORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEYWORDS_FILE.write_text(json.dumps(default_rules, ensure_ascii=False, indent=2), encoding="utf-8")
        _keywords_cache = default_rules
        return default_rules

    # 检查文件是否更新
    mtime = KEYWORDS_FILE.stat().st_mtime
    if mtime != _cache_mtime:
        try:
            _keywords_cache = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
            _cache_mtime = mtime
        except Exception as e:
            logger.error(f"加载关键词文件失败: {e}")
            return _keywords_cache

    return _keywords_cache


async def match_keyword(text: str) -> Optional[str]:
    """
    匹配关键词并返回回复
    mode:
      - contain: 消息包含任一关键词
      - exact: 消息完全匹配关键词
      - regex: 正则匹配
    """
    rules = load_keywords()
    text_lower = text.lower()

    for rule in rules:
        patterns = rule.get("patterns", [])
        mode = rule.get("mode", "contain")
        reply = rule.get("reply", "")

        for pattern in patterns:
            matched = False
            if mode == "contain":
                matched = pattern.lower() in text_lower
            elif mode == "exact":
                matched = text_lower == pattern.lower()
            elif mode == "regex":
                try:
                    matched = bool(re.search(pattern, text, re.IGNORECASE))
                except:
                    pass

            if matched:
                return reply

    return None
