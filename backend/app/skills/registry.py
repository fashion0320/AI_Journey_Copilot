"""Skill 注册与工具导出。

集中管理所有 Skill 实例，提供按名称查询和工具定义导出功能。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.logging import get_logger
from .base import BaseSkill
from .route_master import RouteMasterSkill, get_route_master
from .dynamic_eta import DynamicETASkill, get_dynamic_eta
from .smart_remind import SmartRemindSkill, get_smart_remind
from .local_poi import LocalPOISkill, get_local_poi
from .parking_find import ParkingFindSkill, get_parking_find

logger = get_logger(__name__)


# 所有 Skill 实例（懒加载）
_SKILLS: Dict[str, BaseSkill] = {}
_INITIALIZED = False


def _init_skills() -> None:
    """初始化所有 Skill（懒加载）。"""
    global _INITIALIZED
    if _INITIALIZED:
        return

    _SKILLS["route_master"] = get_route_master()
    _SKILLS["dynamic_eta"] = get_dynamic_eta()
    _SKILLS["smart_remind"] = get_smart_remind()
    _SKILLS["local_poi"] = get_local_poi()
    _SKILLS["parking_find"] = get_parking_find()

    _INITIALIZED = True
    logger.info("all %d skills registered", len(_SKILLS))


def get_skill(name: str) -> Optional[BaseSkill]:
    """按名称获取 Skill。"""
    _init_skills()
    return _SKILLS.get(name)


def get_all_skills() -> Dict[str, BaseSkill]:
    """获取所有 Skill 字典。"""
    _init_skills()
    return dict(_SKILLS)


def get_tools() -> List[Dict[str, Any]]:
    """导出所有 Skill 的 Anthropic tool-use 工具定义列表。

    供 Sprint 4 Agent 注册工具使用。
    """
    _init_skills()
    return [skill.to_tool_definition() for skill in _SKILLS.values()]


def get_tool_handlers() -> Dict[str, Any]:
    """导出 Skill 执行器的 tool_handlers 映射。

    返回 {tool_name: handler_function}，handler 接收 tool_input dict，
    返回结果 dict（SkillResult 的 data 部分）。

    注意：这里返回的是简化版 handler，完整的 Skill 执行
    （含 GCP 上下文、handoff 等）应由 Orchestrator 管理。
    """
    _init_skills()
    handlers = {}
    for name, skill in _SKILLS.items():
        async def _handler(params, _skill=skill):
            result = await _skill.execute(params, gcp_slice={})
            return result.to_dict()
        handlers[name] = _handler
    return handlers
