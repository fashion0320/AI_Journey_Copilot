"""understanding 节点：意图识别 + 信息抽取。

调用 Claude 分析用户输入，判断意图类型（goal_driven / intent_driven），
抽取关键槽位，识别缺失信息。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from langgraph.types import RunnableConfig

from ..prompts import build_intent_prompt, build_system_prompt
from ..state import AgentState
from ...core.logging import get_logger

logger = get_logger(__name__)


async def understanding_node(
    state: AgentState, config: RunnableConfig,
) -> Dict[str, Any]:
    """意图识别节点。

    1. 从 GCP store 取最新快照
    2. 构造意图识别 prompt
    3. 调用 Claude 做意图分类和槽位抽取
    4. 解析 JSON 结果，更新 state
    """
    configurable = config.get("configurable", {}) if config else {}
    claude = configurable.get("claude")
    stream_queue = configurable.get("stream_queue")
    gcp_store = configurable.get("gcp_store")

    # 检查是否追问超限（最多 2 轮，驾驶场景下不宜反复追问）
    if state.get("clarify_count", 0) >= 2:
        msg = "抱歉，我还是不太明白您的需求，您可以换个说法再试一次。"
        if stream_queue:
            try:
                stream_queue.put_nowait(("message", {"role": "assistant", "content": msg}))
                stream_queue.put_nowait(("state_change", {"from_state": "understanding", "to_state": "ended"}))
            except Exception:
                pass
        return {
            "intent_type": "unknown",
            "intent_confidence": 0.0,
            "final_response_text": msg,
            "journey_status": "ended",
        }

    # 澄清回复后的快速路径：如果之前已经有意图，且用户回复了澄清
    # 不要重新让 Claude 解析整个意图（容易产生幻觉），直接用已有信息
    if state.get("user_clarify_reply") and state.get("intent_type"):
        reply = state["user_clarify_reply"].strip()
        existing_dest_name = state.get("destination_name", "")
        intent = state.get("intent_type", "")

        # 如果是目标驱动型且还没有目的地，把用户的回复当作目的地名称
        dest_name = existing_dest_name
        if intent == "goal_driven" and not existing_dest_name and reply:
            dest_name = reply
            # 清理可能的礼貌前缀（如「我要去」「去」）
            for prefix in ["我要去", "我想去", "我去", "去", "到"]:
                if dest_name.startswith(prefix):
                    dest_name = dest_name[len(prefix):].strip()
                    break

        return {
            "intent_type": intent,
            "intent_confidence": state.get("intent_confidence", 0.8),
            "user_intent": state.get("user_intent", ""),
            "destination_name": dest_name,
            "extracted_slots": {
                **(state.get("extracted_slots") or {}),
                "_clarify_reply": reply,
            },
            "missing_slots": [],
            "journey_status": "understanding",
            "user_clarify_reply": "",
            "is_modification": False,
            "modification_type": "",
            "modification_params": {},
        }

    # 如果已经有推荐方案（proposals 不为空）且有新的 user_query，
    # 这是潜在的修改请求 → 必须走 Claude 理解，不能跳过
    existing_proposals = state.get("proposals") or []
    has_new_query = bool(state.get("user_query"))
    if existing_proposals and has_new_query:
        # 有推荐方案 + 新 query → 走完整理解流程，判断是否为修改请求
        pass  # 继续往下执行 Claude 调用
    else:
        # 快速路径：如果目的地已经确认（有有效 lat/lon），
        # 且没有新的 user_query（说明是 clarifying/destination_confirm 之间的流转），
        # 不再重新解析意图
        existing_dest = state.get("destination") or {}
        dest_lat = existing_dest.get("lat")
        dest_lon = existing_dest.get("lon")
        if (dest_lat is not None and dest_lon is not None
                and dest_lat != 0 and dest_lon != 0
                and state.get("intent_type")
                and not has_new_query):
            return {
                "intent_type": state.get("intent_type"),
                "intent_confidence": state.get("intent_confidence", 1.0),
                "user_intent": state.get("user_intent", "travel"),
                "destination_name": state.get("destination_name", ""),
                "extracted_slots": state.get("extracted_slots", {}),
                "missing_slots": [],
                "journey_status": "understanding",
                "user_clarify_reply": "",
                "is_modification": False,
                "modification_type": "",
                "modification_params": {},
            }

    # 取用户输入
    user_query = state.get("user_query", "")
    # 如果有澄清回复，用最近的用户消息
    if state.get("user_clarify_reply"):
        user_query = state["user_clarify_reply"]

    # 取 GCP 快照
    gcp_snapshot = state.get("gcp_snapshot") or {}
    if gcp_store:
        try:
            gcp_snapshot = gcp_store.to_dict()
        except Exception:
            pass

    # 获取对话历史（add_messages reducer 会把 dict 转成 LangChain Message 对象，这里统一转成 dict）
    raw_history = state.get("chat_history") or []
    chat_history = _messages_to_dicts(raw_history)
    existing_slots = state.get("extracted_slots") or {}
    existing_proposals_exist = bool(existing_proposals)

    # 只要已经识别出了意图（intent_type 不是 unknown/空），或有已有推荐，
    # 就把当前信息传给 Claude，让它判断用户是修改还是全新请求
    existing_intent = state.get("intent_type", "") or ""
    has_existing_context = existing_proposals_exist or (existing_intent and existing_intent != "unknown")

    # 如果没有 Claude，走降级逻辑（简单规则判断）
    if claude is None:
        return _fallback_understanding(user_query, gcp_snapshot)

    # 构造 prompt（传入对话历史和已有槽位，支持多轮修改）
    sys_prompt = build_system_prompt(gcp_snapshot, user_query=user_query)
    intent_prompt = build_intent_prompt(
        gcp_snapshot,
        user_query=user_query,
        chat_history=chat_history,
        existing_slots=existing_slots if has_existing_context else None,
        existing_proposals_exist=has_existing_context,
    )

    # 构建消息列表：对话历史中的用户/助手消息作为上下文，最后一条用户消息包含意图分析指令
    messages = _build_messages_with_history(
        chat_history, intent_prompt, user_query,
    )

    # 先尝试降级规则快速判断（对于明显的意图，不需要等 Claude）
    fallback = _fallback_understanding(user_query, gcp_snapshot)

    try:
        # 阶段思考提示
        if stream_queue:
            try:
                if existing_proposals_exist:
                    stream_queue.put_nowait(("thinking_stream", {"text": "正在分析您的新要求..."}))
                else:
                    stream_queue.put_nowait(("thinking_stream", {"text": "正在理解您的出行需求..."}))
            except Exception:
                pass

        # 流式调用，收集完整响应
        full_text_parts: list[str] = []
        # 不推送 token_stream：此节点输出 JSON（非自然语言），避免原始 JSON 泄露到聊天
        async for evt in claude.chat_stream(
            messages, system=sys_prompt,
        ):
            evt_type = evt.type.value if hasattr(evt.type, "value") else evt.type
            if evt_type == "text_delta":
                full_text_parts.append(evt.text)

        full_text = "".join(full_text_parts).strip()

        # 提取 <thinking> 标签内容并推送
        thinking_text, json_text = _extract_thinking(full_text)
        if thinking_text and stream_queue:
            try:
                # 推送结构化思考过程卡片（持久化展示）
                stream_queue.put_nowait(("card_update", {
                    "type": "thought_process",
                    "data": {"content": thinking_text},
                }))
                # 推送思考结束（关闭"思考中..."动画气泡）
                stream_queue.put_nowait(("thinking_end", {}))
            except Exception:
                pass

        result = _parse_intent_json(json_text)
        if result is None:
            # 如果解析 JSON 失败（可能是 thinking 标签格式不同），尝试用原始文本解析
            result = _parse_intent_json(full_text)

        if result is None:
            # 解析失败，降级
            logger.warning("intent JSON parse failed, fallback to rules")
            return _augment_fallback(fallback, gcp_snapshot, stream_queue)

        intent_type = result.get("intent_type", "unknown")
        user_intent = result.get("user_intent", "")
        is_modification = bool(result.get("is_modification", False))
        modification_type = result.get("modification_type", "") or ""
        modification_params = result.get("modification_params", {}) or {}

        # 如果 Claude 返回 unknown 但规则能识别，用规则结果
        if intent_type == "unknown" and fallback.get("intent_type") != "unknown":
            logger.info("Claude returned unknown but rules found intent, using fallback")
            return _augment_fallback(fallback, gcp_snapshot, stream_queue)

        # 如果是全新请求且有已有推荐，清空旧数据
        if is_modification and modification_type == "new_request":
            # 全新请求：视为新旅程，后续逻辑会重置
            existing_proposals_local = state.get("proposals") or []
            if existing_proposals_local:
                # 直接标记为非修改，继续走正常的新意图理解
                is_modification = False
                modification_type = ""
                modification_params = {}

        # 先提取目的地名称和槽位
        extracted = result.get("extracted_slots", {}) or {}
        dest_name = result.get("destination_name", "") or extracted.pop("destination", "")

        # 如果是修改请求，合并修改参数到已有槽位，保持原有意图
        state_intent = state.get("intent_type", "") or ""
        if is_modification and state_intent and state_intent != "unknown":
            intent_type = state_intent
            user_intent = state.get("user_intent", user_intent)
            existing_slot_data = state.get("extracted_slots") or {}
            # 合并：新参数覆盖旧参数
            merged_slots = {**existing_slot_data, **extracted}
            if modification_params:
                merged_slots.update(modification_params)
            extracted = merged_slots
            # 如果目的地之前已有，保持
            existing_dest_name = state.get("destination_name", "")
            if not dest_name and existing_dest_name:
                dest_name = existing_dest_name

        # 意图驱动场景：默认不追问任何槽位 —— 直接推荐比追问体验更好
        missing = result.get("missing_slots", []) or []
        if intent_type == "intent_driven":
            missing = []  # 清空所有缺失槽位，直接推荐

        # 目标驱动场景：只有 destination 是必须的
        if intent_type == "goal_driven":
            missing = [s for s in missing if s == "destination"]

        # 如果 Claude 返回 unknown 且降级规则也没识别出来，
        # 直接结束并友好提示，避免卡在 "理解中" 状态
        if intent_type == "unknown":
            msg = "抱歉，我没太明白您的出行需求，可以再说详细一点吗？比如想去哪里，或者想做什么？"
            if stream_queue:
                try:
                    stream_queue.put_nowait(("message", {"role": "assistant", "content": msg}))
                    stream_queue.put_nowait(("state_change", {"from_state": "understanding", "to_state": "ended"}))
                except Exception:
                    pass
            return {
                "intent_type": "unknown",
                "intent_confidence": result.get("confidence", 0.0),
                "user_intent": "",
                "destination_name": "",
                "extracted_slots": extracted,
                "missing_slots": [],
                "final_response_text": msg,
                "journey_status": "ended",
                "gcp_snapshot": gcp_snapshot,
                "user_clarify_reply": "",
                "is_modification": False,
                "modification_type": "",
                "modification_params": {},
            }

        # 推送理解确认消息
        if stream_queue:
            try:
                if is_modification:
                    # 修改请求：推送确认消息
                    confirm_msg = _build_modification_confirm_msg(
                        modification_type, modification_params, extracted,
                    )
                    stream_queue.put_nowait(("message", {
                        "role": "assistant",
                        "content": confirm_msg,
                    }))
                elif not state.get("user_clarify_reply"):
                    # 首次交互：推送简洁确认
                    if dest_name:
                        msg = f"好的，帮您前往{dest_name}"
                    else:
                        msg = "好的，帮您推荐附近的好地方"
                    stream_queue.put_nowait(("message", {
                        "role": "assistant",
                        "content": msg,
                    }))
            except Exception:
                pass

        return {
            "intent_type": intent_type,
            "intent_confidence": result.get("confidence", 0.0),
            "user_intent": user_intent,
            "destination_name": dest_name,
            "extracted_slots": extracted,
            "missing_slots": missing,
            "journey_status": "understanding",
            "gcp_snapshot": gcp_snapshot,
            "user_clarify_reply": "",  # 消费掉澄清回复
            "is_modification": is_modification,
            "modification_type": modification_type,
            "modification_params": modification_params,
            # 修改时清空旧的 proposals，触发重新推荐
            "proposals": [] if is_modification else state.get("proposals", []),
        }

    except Exception as e:
        logger.exception("understanding node error: %s", e)
        return _augment_fallback(fallback, gcp_snapshot, stream_queue)


def _extract_thinking(text: str) -> tuple[str, str]:
    """从文本中提取 <thinking> 标签内容。

    Returns:
        (thinking_content, remaining_json_text)
    """
    THINKING_OPEN = "<thinking>"
    THINKING_CLOSE = "</thinking>"
    open_idx = text.find(THINKING_OPEN)
    close_idx = text.find(THINKING_CLOSE)
    if open_idx >= 0 and close_idx > open_idx:
        thinking = text[open_idx + len(THINKING_OPEN):close_idx].strip()
        remaining = (text[:open_idx] + text[close_idx + len(THINKING_CLOSE):]).strip()
        return thinking, remaining
    return "", text


def _build_modification_confirm_msg(
    modification_type: str,
    modification_params: Dict[str, Any],
    extracted_slots: Dict[str, Any],
) -> str:
    """根据修改类型生成确认消息。"""
    budget_level = (extracted_slots or {}).get("budget_level", "")
    max_price = (modification_params or {}).get("max_price_per_person")
    cuisine = (modification_params or {}).get("cuisine_type", "") or (extracted_slots or {}).get("cuisine_type", "")

    if modification_type == "budget_change":
        if max_price:
            return f"好的，帮您重新找一些人均{int(max_price)}元以下的好地方"
        if budget_level == "budget":
            return "好的，帮您推荐一些性价比高的经济之选"
        if budget_level == "premium":
            return "好的，帮您找一些品质更好的高端选择"
        return "好的，帮您按新的预算重新推荐"
    elif modification_type == "cuisine_change" and cuisine:
        return f"好的，换{cuisine}看看，帮您找几家不错的"
    elif modification_type == "distance_change":
        return "好的，帮您找一些更近的"
    elif modification_type == "other_preference":
        return "好的，按您的要求重新筛选"
    return "好的，帮您重新推荐"


def _messages_to_dicts(messages: list) -> list[dict]:
    """将 LangChain Message 对象 / dict 混合列表统一转为 dict 列表。

    add_messages reducer 会把传入的 dict 转成 HumanMessage/AIMessage/SystemMessage，
    所以读取 chat_history 时需要兼容两种类型。
    """
    result = []
    for m in messages:
        if isinstance(m, dict):
            result.append(m)
        else:
            # LangChain BaseMessage：type 属性是 "human" / "ai" / "system"
            try:
                msg_type = getattr(m, "type", "")
                role_map = {"human": "user", "ai": "assistant", "system": "system"}
                role = role_map.get(msg_type, msg_type or "user")
                content = getattr(m, "content", "")
                result.append({"role": role, "content": content if isinstance(content, str) else str(content)})
            except Exception:
                continue
    return result


def _build_messages_with_history(
    chat_history: list[dict],
    intent_prompt: str,
    user_query: str,
) -> list[dict]:
    """构建带对话历史的 messages 列表。

    将最近的对话历史作为上下文，最后一条消息是意图分析指令 + 用户输入。
    """
    messages = []
    # 取最近几条有意义的对话消息（跳过系统消息）
    history_to_use = []
    for msg in chat_history[-8:]:  # 最多取最近8条
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            history_to_use.append({"role": role, "content": content[:500]})

    # 如果有历史，把它们作为上下文
    if history_to_use:
        # 构建一条包含历史的上下文消息
        context_lines = []
        for h in history_to_use[:-1]:  # 除了最后一条（就是当前user_query）
            role_label = "用户" if h["role"] == "user" else "助手"
            context_lines.append(f"{role_label}：{h['content']}")
        if context_lines:
            messages.append({
                "role": "user",
                "content": "以下是之前的对话记录，请结合上下文理解用户最新的需求：\n\n" + "\n".join(context_lines),
            })
            messages.append({
                "role": "assistant",
                "content": "好的，我会结合对话历史来理解。请告诉我最新的输入。",
            })

    # 最后一条消息包含意图分析指令和用户最新输入
    messages.append({
        "role": "user",
        "content": f"{intent_prompt}\n\n用户最新输入：{user_query}",
    })
    return messages


def _parse_intent_json(text: str) -> Dict[str, Any] | None:
    """从 Claude 返回的文本中解析 JSON。

    处理可能的 markdown code block 包裹。
    """
    text = text.strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` 块
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.find("```", start)
        if end > start:
            try:
                return json.loads(text[start:end].strip())
            except json.JSONDecodeError:
                pass
    # 尝试第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _fallback_understanding(query: str, gcp_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """降级的规则意图识别（无 Claude 时使用）。"""
    # 简单关键词匹配
    goal_keywords = ["去", "到", "接", "送", "前往", "出发去", "回家", "回公司", "去公司",
                     "机场", "车站", "火车站", "高铁站"]
    # 意图驱动关键词（找地方做某事）
    intent_keywords = [
        "吃饭", "餐厅", "咖啡", "喝一杯", "喝点", "喝酒", "酒吧", "小酌", "微醺",
        "逛街", "买东西", "购物", "玩", "休闲", "找个地方", "有没有", "推荐",
        "聚聚", "聚会", "闺蜜", "朋友", "宵夜", "夜宵", "喝一杯", "喝什么",
    ]
    # 纯意图关键词（没有目的地动词）
    pure_intent_markers = [
        "喝一杯", "喝点", "喝酒", "小酌", "微醺", "吃什么", "吃点", "想吃",
        "去哪玩", "找个地方", "有没有", "推荐", "聚聚", "聚会",
        "宵夜", "夜宵", "饿了", "渴了",
    ]

    is_goal = any(kw in query for kw in goal_keywords)
    is_intent = any(kw in query for kw in intent_keywords)
    is_pure_intent = any(kw in query for kw in pure_intent_markers)

    intent_type = "unknown"
    user_intent = "unknown"
    missing_slots: list[str] = []

    if is_pure_intent:
        # 纯意图表达（如"喝一杯"），一定是意图驱动
        intent_type = "intent_driven"
        if any(kw in query for kw in ["喝", "酒", "酒吧", "小酌", "微醺"]):
            user_intent = "drinks"
        elif any(kw in query for kw in ["咖啡"]):
            user_intent = "coffee"
        elif any(kw in query for kw in ["吃", "餐", "饭", "饿", "餐厅", "宵夜", "夜宵"]):
            user_intent = "dining"
        elif any(kw in query for kw in ["逛街", "买东西", "购物"]):
            user_intent = "shopping"
        else:
            user_intent = "leisure"
    elif is_goal and not is_intent:
        intent_type = "goal_driven"
        user_intent = "travel"
    elif is_intent and not is_goal:
        intent_type = "intent_driven"
        if any(kw in query for kw in ["喝", "酒", "酒吧", "小酌", "微醺"]):
            user_intent = "drinks"
        elif any(kw in query for kw in ["吃", "餐", "饭", "饿", "餐厅", "宵夜", "夜宵"]):
            user_intent = "dining"
        elif any(kw in query for kw in ["咖啡"]):
            user_intent = "coffee"
        elif any(kw in query for kw in ["逛街", "买东西", "购物"]):
            user_intent = "shopping"
        else:
            user_intent = "leisure"
    elif is_goal and is_intent:
        # 都有，按更明显的判断
        goal_count = sum(1 for kw in ["去", "到", "接", "送", "前往"] if kw in query)
        intent_count = sum(1 for kw in ["吃", "喝", "玩", "逛", "买", "推荐", "聚"] if kw in query)
        if goal_count > intent_count:
            intent_type = "goal_driven"
            user_intent = "travel"
        else:
            intent_type = "intent_driven"
            if any(kw in query for kw in ["喝", "酒", "酒吧"]):
                user_intent = "drinks"
            elif "吃" in query:
                user_intent = "dining"
            else:
                user_intent = "leisure"

    # 提取目的地（简单匹配「去X」「到X」「接X」的 X）
    dest_name = ""
    if intent_type == "goal_driven":
        for marker in ["去", "到", "接"]:
            idx = query.find(marker)
            if idx >= 0 and idx + len(marker) < len(query):
                rest = query[idx + len(marker):].strip()
                # 取第一个停顿前的内容
                for stop in ["，", "。", "？", "吗", "吧", "喝", "吃", "玩", "和", "跟", " "]:
                    stop_idx = rest.find(stop)
                    if stop_idx > 0:
                        rest = rest[:stop_idx].strip()
                        break
                # 排除太短或明显是动词/疑问词的片段
                if rest and 1 < len(rest) < 30 and rest not in ("哪", "哪儿", "哪里", "什么", "一杯"):
                    dest_name = rest
                    break

    # 只有目标驱动型需要目的地
    if intent_type == "goal_driven" and not dest_name:
        missing_slots.append("destination")
    # 意图驱动场景：直接推荐，不追问任何非必要信息

    return {
        "intent_type": intent_type,
        "intent_confidence": 0.6 if is_pure_intent else 0.5,
        "user_intent": user_intent,
        "destination_name": dest_name,
        "extracted_slots": {},
        "missing_slots": missing_slots,
        "journey_status": "understanding",
    }


def _augment_fallback(
    fallback: Dict[str, Any],
    gcp_snapshot: Dict[str, Any],
    stream_queue: Any,
) -> Dict[str, Any]:
    """对降级结果做增强：补充 GCP 快照、推送消息等。"""
    intent_type = fallback.get("intent_type", "unknown")

    # 如果还是 unknown，推送友好提示并结束
    if intent_type == "unknown":
        msg = "抱歉，我没太明白您的出行需求，可以再说详细一点吗？比如想去哪里，或者想做什么？"
        if stream_queue:
            try:
                stream_queue.put_nowait(("message", {"role": "assistant", "content": msg}))
                stream_queue.put_nowait(("state_change", {"from_state": "understanding", "to_state": "ended"}))
            except Exception:
                pass
        return {
            **fallback,
            "final_response_text": msg,
            "journey_status": "ended",
        }

    # 有明确意图时，推送简洁确认消息
    if stream_queue:
        try:
            dest_name = fallback.get("destination_name", "")
            if dest_name:
                msg = f"好的，帮您规划前往{dest_name}的路线"
            else:
                msg = "好的，我来帮您推荐附近的好地方"
            stream_queue.put_nowait(("message", {"role": "assistant", "content": msg}))
        except Exception:
            pass

    return {
        **fallback,
        "gcp_snapshot": gcp_snapshot,
    }
