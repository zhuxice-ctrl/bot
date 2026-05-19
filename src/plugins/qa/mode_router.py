"""会话模式相关的纯函数。"""
from __future__ import annotations


def build_tk_mode_reply(text: str, has_report_context: bool = False) -> str:
    """构造 /tk 视频报告模式下的普通文本回复。"""
    clean = (text or "").strip()
    if has_report_context:
        return (
            "我已经有上一份视频报告的上下文。你可以直接追问这条视频的选题、脚本结构、爆点、受众、账号适配度或改写方向。\n"
            "如果要生成新报告，直接发送新的抖音分享文本或链接。"
        )
    if any(keyword in clean for keyword in ("会什么", "能做什么", "怎么用", "帮助", "功能")):
        return (
            "我现在处于 /tk 视频报告模式。\n"
            "你可以直接发送抖音分享文本或链接，我会生成短视频分析报告，并附上生成日期、标题、作者、报告编号和原链接，方便后续检索。\n"
            "连续发送多个链接也可以，系统会按队列生成，避免服务器转写任务过载。\n"
            "发送 /回顾 可以查看上一份还没细致追问的报告；发送 /shop 可以切回商品/发货模式。"
        )
    return (
        "当前是 /tk 视频报告模式。请直接发送抖音分享文本或链接，我会生成报告。\n"
        "如果要继续追问上一份报告，发送 /回顾；如果要买商品或查订单，发送 /shop 切回商品模式。"
    )


def build_video_report_followup_messages(
    context: dict[str, object],
    question: str,
) -> list[dict[str, str]]:
    """构造基于上一份视频报告上下文的追问消息。"""
    system_prompt = (
        "你是短视频报告复盘助手。只能基于提供的报告上下文、元数据和语音转写回答用户追问。"
        "先在内部按证据链核对: 用户问题 -> 可用证据 -> 判断 -> 不确定项，但不要输出内部思维链。"
        "最终用简洁中文回答，优先给可执行建议。"
        "回答结构按需要使用: 结论、依据、不确定点、下一步。"
        "如果上下文没有证据，明确说当前报告里没有足够证据，不要编造。"
    )
    user_prompt = (
        f"用户问题:\n{question.strip()}\n\n"
        "上一份报告上下文:\n"
        f"标题: {context.get('title') or '未命名视频'}\n"
        f"报告编号: {context.get('report_id') or '未知'}\n"
        f"生成时间: {context.get('generated_at_text') or '未知'}\n"
        f"链接: {context.get('url') or '未知'}\n\n"
        f"【报告正文】\n{context.get('report') or '无'}\n\n"
        f"【元数据】\n{context.get('metadata_summary') or '无'}\n\n"
        f"【语音转写】\n{context.get('transcript') or '无'}\n\n"
        f"【已知不足】\n{context.get('warnings') or '无'}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_video_report_fallback_reply(context: dict[str, object], question: str) -> str:
    """模型不可用时，用已有报告上下文给出基础追问回复。"""
    title = str(context.get("title") or "未命名视频")
    report_id = str(context.get("report_id") or "未知")
    report = str(context.get("report") or "").strip()
    summary = report[:500].rstrip() if report else "当前只保存了检索信息，没有足够的报告正文。"
    return (
        f"我会基于上一份报告《{title}》（{report_id}）回答。\n"
        f"你的问题: {question.strip()}\n\n"
        "当前可用依据:\n"
        f"{summary}\n\n"
        "模型暂时不可用，所以这里只能给基础上下文摘要。你可以稍后再问，我会继续围绕这份报告回答。"
    )
