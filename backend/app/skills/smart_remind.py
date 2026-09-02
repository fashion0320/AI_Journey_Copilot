"""Skill_Smart_Remind —— 智能提醒中枢。

生成各类场景化提醒，输出 TTS 播报文案和 UI 展示数据。
支持：出发前提醒、天气提醒、行程中播报、事件变化、
      航班动态、到达前提醒。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.logging import get_logger
from .base import BaseSkill, SkillResult, SkillStatus

logger = get_logger(__name__)


class SmartRemindSkill(BaseSkill):
    """智能提醒中枢 Skill。"""

    name = "smart_remind"
    description = (
        "智能提醒生成服务。根据场景生成自然语言提醒文案，"
        "支持出发前提醒、天气提醒、行程中ETA变化播报、"
        "航班/事件变化通知、到达前提醒等。"
        "返回提醒标题、详细内容、TTS播报文本和优先级。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "remind_type": {
                "type": "string",
                "enum": [
                    "pre_departure",
                    "weather",
                    "in_journey",
                    "event_change",
                    "transit_dynamic",
                    "pre_arrival",
                ],
                "description": "提醒类型",
            },
            "destination": {
                "type": "string",
                "description": "目的地名称",
            },
            "weather": {
                "type": "object",
                "properties": {
                    "weather": {"type": "string", "description": "天气状况"},
                    "temperature": {"type": "number", "description": "温度"},
                    "windpower": {"type": "string", "description": "风力"},
                },
                "description": "天气信息",
            },
            "departure_time": {
                "type": "string",
                "description": "出发时间（如 14:30）",
            },
            "items_to_bring": {
                "type": "array",
                "items": {"type": "string"},
                "description": "需携带的物品列表",
            },
            "is_driving": {
                "type": "boolean",
                "default": True,
                "description": "是否驾驶场景",
            },
            "eta_delta_min": {
                "type": "number",
                "description": "ETA 变化分钟数（正数=变慢，负数=变快）",
            },
            "current_traffic": {
                "type": "string",
                "description": "当前交通状况",
            },
            "next_stop": {
                "type": "string",
                "description": "下一站名称",
            },
            "event_type": {
                "type": "string",
                "description": "事件类型（flight/meeting等）",
            },
            "event_name": {
                "type": "string",
                "description": "事件名称",
            },
            "old_value": {
                "type": "string",
                "description": "原值",
            },
            "new_value": {
                "type": "string",
                "description": "新值",
            },
            "impact": {
                "type": "string",
                "description": "影响描述",
            },
            "flight_no": {
                "type": "string",
                "description": "航班号",
            },
            "flight_status": {
                "type": "string",
                "description": "航班状态",
            },
            "delay_min": {
                "type": "number",
                "description": "延误分钟数",
            },
            "terminal": {
                "type": "string",
                "description": "航站楼",
            },
            "gate": {
                "type": "string",
                "description": "登机口",
            },
            "parking_info": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "walk_min": {"type": "number"},
                },
                "description": "停车场信息",
            },
            "eta_min": {
                "type": "number",
                "description": "剩余到达分钟数",
            },
            "next_action": {
                "type": "string",
                "description": "下一步动作提示",
            },
        },
        "required": ["remind_type"],
    }
    gcp_dependencies = [
        "weather.live",
        "time.datetime_iso",
        "transit",
    ]

    async def execute(
        self,
        params: Dict[str, Any],
        gcp_slice: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        remind_type = params.get("remind_type", "")

        try:
            if remind_type == "pre_departure":
                return self._remind_pre_departure(params, gcp_slice)
            elif remind_type == "weather":
                return self._remind_weather(params, gcp_slice)
            elif remind_type == "in_journey":
                return self._remind_in_journey(params)
            elif remind_type == "event_change":
                return self._remind_event_change(params)
            elif remind_type == "transit_dynamic":
                return self._remind_transit_dynamic(params)
            elif remind_type == "pre_arrival":
                return self._remind_pre_arrival(params)
            else:
                return SkillResult.error(f"unknown remind_type: {remind_type}")
        except Exception as e:
            logger.exception("smart_remind error: %s", e)
            return SkillResult.error(str(e), "提醒生成出错")

    # ==================== pre_departure ====================

    def _remind_pre_departure(
        self, params: Dict[str, Any], gcp_slice: Dict[str, Any]
    ) -> SkillResult:
        destination = params.get("destination", "目的地")
        departure_time = params.get("departure_time", "")
        items = params.get("items_to_bring", [])
        weather = params.get("weather") or {}

        # 从 GCP 获取天气（如果参数中没有）
        if not weather:
            try:
                weather = gcp_slice["weather"]["live"]
            except (KeyError, TypeError):
                weather = {}

        weather_desc = ""
        if weather:
            w = weather.get("weather", "")
            temp = weather.get("temperature", "")
            if w and temp:
                weather_desc = f"，今天{w}，气温{temp}度"

        # 物品提醒
        items_text = ""
        if items:
            items_text = f"，请记得带上{'、'.join(items)}"

        time_text = f"{departure_time}出发" if departure_time else "准备出发"

        title = f"出发提醒：{destination}"
        tts_text = f"您好，您即将前往{destination}，{time_text}{weather_desc}{items_text}。祝您一路平安。"

        result = {
            "title": title,
            "message": tts_text,
            "tts_text": tts_text,
            "priority": "high" if items else "medium",
            "remind_type": "pre_departure",
        }

        return SkillResult.success(result, tts_text)

    # ==================== weather ====================

    def _remind_weather(
        self, params: Dict[str, Any], gcp_slice: Dict[str, Any]
    ) -> SkillResult:
        weather = params.get("weather") or {}
        is_driving = params.get("is_driving", True)

        # 从 GCP 补充
        if not weather:
            try:
                weather = gcp_slice["weather"]["live"]
            except (KeyError, TypeError):
                weather = {}

        w = str(weather.get("weather", ""))
        temp = weather.get("temperature", "")
        wind = str(weather.get("windpower", ""))

        # 判断严重程度
        severity = "info"
        driving_tips = ""
        title = "天气提醒"

        # 恶劣天气判断
        if any(k in w for k in ["暴雨", "大暴雨", "雷暴", "暴雪", "大雾", "霾", "沙尘暴"]):
            severity = "danger"
            title = "恶劣天气预警"
            driving_tips = "请减速慢行，保持安全车距，注意行车安全。"
        elif any(k in w for k in ["雨", "雪", "雾", "雷"]):
            severity = "warning"
            title = "天气变化提醒"
            driving_tips = "路面湿滑，请小心驾驶。"
        elif any(k in w for k in ["大风", "风"] + (["≥5"] if wind else [])):
            severity = "warning"
            title = "大风提醒"
            driving_tips = "请注意横风影响，握稳方向盘。"
        else:
            severity = "info"
            title = "天气播报"

        # 风力等级判断
        try:
            wind_level = int(wind.replace("级", "").replace("-", "").split("~")[-1])
            if wind_level >= 6:
                severity = "warning"
                driving_tips = driving_tips or "风力较大，请注意横风影响。"
        except (ValueError, IndexError):
            pass

        weather_desc = f"当前{w}，气温{temp}度"
        if wind:
            weather_desc += f"，风力{wind}级"

        if is_driving and driving_tips:
            tts_text = f"{title}：{weather_desc}。{driving_tips}"
        else:
            tts_text = f"{title}：{weather_desc}。"

        message = weather_desc
        if driving_tips and is_driving:
            message += f"\n驾驶建议：{driving_tips}"

        result = {
            "title": title,
            "message": message,
            "tts_text": tts_text,
            "severity": severity,
            "weather": weather,
            "remind_type": "weather",
        }

        return SkillResult.success(result, tts_text)

    # ==================== in_journey ====================

    def _remind_in_journey(self, params: Dict[str, Any]) -> SkillResult:
        eta_delta = params.get("eta_delta_min", 0)
        traffic = params.get("current_traffic", "")
        next_stop = params.get("next_stop", "")

        if eta_delta == 0:
            # 常规播报
            if next_stop:
                tts_text = f"前方行驶顺畅，预计将准时到达{next_stop}。"
            else:
                tts_text = "前方行驶顺畅，继续保持安全驾驶。"
            urgency = "normal"
            title = "行程播报"
        elif eta_delta > 0:
            # 变慢
            if eta_delta >= 15:
                level = "严重"
                urgency = "elevated"
            elif eta_delta >= 5:
                level = "一定"
                urgency = "normal"
            else:
                level = "轻微"
                urgency = "normal"

            reason = f"受{traffic}影响" if traffic else "受路况影响"
            if next_stop:
                tts_text = f"提醒您，{reason}，到达{next_stop}的时间将推迟约{eta_delta}分钟，请耐心驾驶。"
            else:
                tts_text = f"提醒您，{reason}，到达时间将推迟约{eta_delta}分钟。"
            title = f"行程延误约{eta_delta}分钟"
        else:
            # 变快
            delta = abs(eta_delta)
            if next_stop:
                tts_text = f"好消息，目前路况顺畅，到达{next_stop}的时间预计提前{delta}分钟。"
            else:
                tts_text = f"好消息，目前路况顺畅，到达时间预计提前{delta}分钟。"
            urgency = "normal"
            title = f"行程提前约{delta}分钟"

        result = {
            "title": title,
            "message": tts_text,
            "tts_text": tts_text,
            "urgency": urgency,
            "eta_delta_min": eta_delta,
            "next_stop": next_stop,
            "remind_type": "in_journey",
        }

        return SkillResult.success(result, tts_text)

    # ==================== event_change ====================

    def _remind_event_change(self, params: Dict[str, Any]) -> SkillResult:
        event_type = params.get("event_type", "事件")
        event_name = params.get("event_name", "")
        old_value = params.get("old_value", "")
        new_value = params.get("new_value", "")
        impact = params.get("impact", "")

        event_type_cn = {
            "flight": "航班",
            "meeting": "会议",
            "reservation": "预约",
        }.get(event_type, event_type)

        title = f"{event_type_cn}变动通知"

        tts_parts = [f"提醒您，{event_name}有变动。"]
        if old_value and new_value:
            tts_parts.append(f"{event_type_cn}时间从{old_value}调整为{new_value}。")
        elif new_value:
            tts_parts.append(f"最新{event_type_cn}时间是{new_value}。")

        if impact:
            tts_parts.append(f"影响：{impact}。")
            title += f"：{impact}"

        action_suggestion = ""
        if event_type == "flight":
            if "延误" in (impact or "") or "delay" in (impact or "").lower():
                action_suggestion = "建议您稍后出发，实时关注航班动态。"
            elif "取消" in (impact or ""):
                action_suggestion = "建议您立即改签到其他航班或调整出行计划。"
        if action_suggestion:
            tts_parts.append(action_suggestion)

        tts_text = "".join(tts_parts)

        result = {
            "title": title,
            "message": tts_text,
            "tts_text": tts_text,
            "event_type": event_type,
            "old_value": old_value,
            "new_value": new_value,
            "action_suggestion": action_suggestion or None,
            "remind_type": "event_change",
        }

        return SkillResult.success(result, tts_text)

    # ==================== transit_dynamic ====================

    def _remind_transit_dynamic(self, params: Dict[str, Any]) -> SkillResult:
        flight_no = params.get("flight_no", "")
        status = params.get("flight_status", "")
        delay_min = params.get("delay_min", 0)
        terminal = params.get("terminal", "")
        gate = params.get("gate", "")

        title = f"航班动态：{flight_no}"

        status_cn = {
            "scheduled": "计划中",
            "delayed": "延误",
            "boarding": "登机中",
            "departed": "已起飞",
            "arrived": "已到达",
            "cancelled": "已取消",
        }.get(status, status)

        tts_parts = [f"航班{flight_no}最新动态："]
        tts_parts.append(f"目前状态{status_cn}。")

        if delay_min and delay_min > 0:
            tts_parts.append(f"预计延误约{delay_min}分钟。")

        if terminal:
            tts_parts.append(f"航站楼：{terminal}。")
        if gate:
            tts_parts.append(f"登机口：{gate}。")

        updated_eta = None
        action_suggestion = None

        if status == "delayed" and delay_min:
            action_suggestion = f"航班延误{delay_min}分钟，请合理安排出发时间。"
        elif status == "boarding":
            action_suggestion = "航班正在登机，请尽快前往登机口。"
        elif status == "arrived":
            action_suggestion = "航班已到达，可以准备接人了。"

        if action_suggestion:
            tts_parts.append(action_suggestion)

        tts_text = "".join(tts_parts)

        result = {
            "title": title,
            "message": tts_text,
            "tts_text": tts_text,
            "flight_no": flight_no,
            "status": status,
            "delay_min": delay_min,
            "terminal": terminal,
            "gate": gate,
            "updated_eta": updated_eta,
            "action_suggestion": action_suggestion,
            "remind_type": "transit_dynamic",
        }

        return SkillResult.success(result, tts_text)

    # ==================== pre_arrival ====================

    def _remind_pre_arrival(self, params: Dict[str, Any]) -> SkillResult:
        destination = params.get("destination", "目的地")
        parking_info = params.get("parking_info") or {}
        eta_min = params.get("eta_min", 0)
        next_action = params.get("next_action", "")

        title = f"即将到达：{destination}"

        tts_parts = []
        if eta_min > 0:
            tts_parts.append(f"还有约{eta_min}分钟到达{destination}。")
        else:
            tts_parts.append(f"即将到达{destination}。")

        preparation_tips: List[str] = []

        if parking_info:
            p_name = parking_info.get("name", "")
            walk_min = parking_info.get("walk_min", 0)
            if p_name:
                tts_parts.append(f"推荐停车场：{p_name}。")
                if walk_min:
                    tts_parts.append(f"下车后步行约{walk_min}分钟到达。")
                preparation_tips.append(f"停车场：{p_name}")

        if next_action:
            tts_parts.append(f"下一步：{next_action}")
            preparation_tips.append(next_action)

        # 通用准备提醒
        preparation_tips.append("请携带好随身物品")
        tts_parts.append("请携带好随身物品，准备下车。")

        tts_text = "".join(tts_parts)

        result = {
            "title": title,
            "message": tts_text,
            "tts_text": tts_text,
            "destination": destination,
            "parking_info": parking_info,
            "eta_min": eta_min,
            "preparation_tips": preparation_tips,
            "remind_type": "pre_arrival",
        }

        return SkillResult.success(result, tts_text)


# 全局实例
_smart_remind: Optional[SmartRemindSkill] = None


def get_smart_remind() -> SmartRemindSkill:
    global _smart_remind
    if _smart_remind is None:
        _smart_remind = SmartRemindSkill()
    return _smart_remind
