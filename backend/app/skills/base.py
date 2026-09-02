"""Skill 基类与公共类型定义。

所有 Skill 继承 BaseSkill，统一输入输出格式，
便于 Agent 通过 tool-use 调用和 Orchestrator 编排。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..core.logging import get_logger

logger = get_logger(__name__)


class SkillStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"   # 部分成功（如部分数据缺失）
    ERROR = "error"
    NO_RESULT = "no_result"  # 无结果（正常，但没有找到数据）


@dataclass
class SkillResult:
    """Skill 执行结果。"""
    status: SkillStatus = SkillStatus.SUCCESS
    data: Dict[str, Any] = field(default_factory=dict)
    message_template: str = ""   # TTS 播报模板（带占位符）
    error_msg: Optional[str] = None   # 错误信息（字段名避免与类方法 error() 冲突）
    card_data: Optional[Dict[str, Any]] = None  # UI 卡片数据（Sprint 5 用）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value if hasattr(self.status, "value") else str(self.status),
            "data": self.data,
            "message_template": self.message_template,
            "error": self.error_msg,
            "card_data": self.card_data,
        }

    @classmethod
    def success(cls, data: Dict[str, Any], message: str = "") -> "SkillResult":
        return cls(status=SkillStatus.SUCCESS, data=data, message_template=message)

    @classmethod
    def partial(cls, data: Dict[str, Any], message: str = "", error: str = "") -> "SkillResult":
        return cls(status=SkillStatus.PARTIAL, data=data, message_template=message, error_msg=error)

    @classmethod
    def no_result(cls, message: str = "") -> "SkillResult":
        return cls(status=SkillStatus.NO_RESULT, message_template=message)

    @classmethod
    def error(cls, error: str, message: str = "") -> "SkillResult":
        return cls(status=SkillStatus.ERROR, error_msg=error, message_template=message)


class BaseSkill(ABC):
    """Skill 抽象基类。

    子类需要实现：
    - name: Skill 名称（作为 tool-use 的 tool name）
    - description: Skill 描述（用于 LLM 的 tool 描述）
    - input_schema: JSON Schema 格式的输入定义
    - gcp_dependencies: 依赖的 GCP 字段路径列表
    - execute(): 执行 Skill 的主方法
    """

    name: str = ""
    description: str = ""
    input_schema: Dict[str, Any] = {}
    gcp_dependencies: List[str] = []  # 如 ["vehicle.position", "weather.live.weather"]

    @abstractmethod
    async def execute(
        self,
        params: Dict[str, Any],
        gcp_slice: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        """执行 Skill。

        Args:
            params: 调用参数（来自 tool_use 的 input）
            gcp_slice: GCP 上下文子集（根据 gcp_dependencies 截取）
            context: 额外上下文（如前一个 Skill 的结果、旅程状态等）

        Returns:
            SkillResult
        """
        ...

    def to_tool_definition(self) -> Dict[str, Any]:
        """转换为 Anthropic tool-use 格式的工具定义。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def extract_gcp_slice(self, global_ctx: Dict[str, Any]) -> Dict[str, Any]:
        """从全局 GCP 上下文中截取本 Skill 依赖的字段。

        简单实现：按字段路径逐级取值。
        """
        result: Dict[str, Any] = {}
        for field_path in self.gcp_dependencies:
            parts = field_path.split(".")
            val: Any = global_ctx
            for part in parts:
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = getattr(val, part, None)
                if val is None:
                    break
            if val is not None:
                # 放到结果中，保持路径结构
                dst = result
                for i, part in enumerate(parts[:-1]):
                    if part not in dst:
                        dst[part] = {}
                    dst = dst[part]
                dst[parts[-1]] = val
        return result
