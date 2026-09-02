"""Skills —— 5 个核心能力模块。

BaseSkill 定义统一接口，各 Skill 封装具体业务逻辑，
registry 提供统一注册和工具导出。
"""

from .base import BaseSkill, SkillResult, SkillStatus
from .route_master import RouteMasterSkill, get_route_master
from .dynamic_eta import DynamicETASkill, get_dynamic_eta
from .smart_remind import SmartRemindSkill, get_smart_remind
from .local_poi import LocalPOISkill, get_local_poi
from .parking_find import ParkingFindSkill, get_parking_find
from .registry import get_all_skills, get_skill, get_tools, get_tool_handlers

__all__ = [
    "BaseSkill",
    "SkillResult",
    "SkillStatus",
    "RouteMasterSkill",
    "DynamicETASkill",
    "SmartRemindSkill",
    "LocalPOISkill",
    "ParkingFindSkill",
    "get_route_master",
    "get_dynamic_eta",
    "get_smart_remind",
    "get_local_poi",
    "get_parking_find",
    "get_all_skills",
    "get_skill",
    "get_tools",
    "get_tool_handlers",
]
