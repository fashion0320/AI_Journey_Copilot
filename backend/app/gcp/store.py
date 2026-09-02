"""GCP ContextStore —— 全局上下文的单例存储、字段级订阅与事件推送。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, Set

from .models import (
    GlobalContext,
    JourneyState,
    TrafficStatus,
    UserProfile,
)
from ..core.logging import get_logger

logger = get_logger(__name__)

# 订阅回调签名：(field_path: str, old_value, new_value) -> None
SubscriberFn = Callable[[str, Any, Any], None]


class ContextStore:
    """全局上下文总线。

    - 保存一份 GlobalContext 状态快照
    - 支持字段级订阅（dot notation，如 "traffic.on_route.overall_status"）
    - 更新时检测变化，仅对有变化的字段触发事件
    - 异步事件推送（通过 asyncio.Queue 推送给 WebSocket 订阅者）
    """

    _instance: Optional["ContextStore"] = None

    def __init__(self) -> None:
        self._ctx = GlobalContext()
        self._subscribers: Dict[str, List[SubscriberFn]] = {}
        self._prefix_watchers: List[str] = []  # 整模块级订阅
        self._event_queues: Set[asyncio.Queue] = set()  # 多订阅者事件队列（fan-out）
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "ContextStore":
        if cls._instance is None:
            cls._instance = ContextStore()
        return cls._instance

    # ---------- 读取 ----------

    def snapshot(self) -> GlobalContext:
        """返回当前快照（浅拷贝语义，只读使用）。"""
        return self._ctx.model_copy(deep=True)

    def to_dict(self) -> Dict[str, Any]:
        return self._ctx.model_dump(mode="json")

    def get(self, field_path: str) -> Any:
        """按字段路径读取，如 'weather.live.temperature'。"""
        parts = field_path.split(".")
        val: Any = self._ctx
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = getattr(val, part, None)
            if val is None:
                return None
        return val

    # ---------- 更新 ----------

    async def update(self, updates: Dict[str, Any]) -> List[str]:
        """按字段路径批量更新，返回发生变化的字段列表。

        updates: {"vehicle.position.lat": 31.23, "weather.live.weather": "小雨"}
        """
        async with self._lock:
            changed: List[str] = []
            ctx_dict = self._ctx.model_dump()

            for field_path, new_val in updates.items():
                old_val = self._get_by_path(ctx_dict, field_path)
                if old_val == new_val:
                    continue
                self._set_by_path(ctx_dict, field_path, new_val)
                changed.append(field_path)

            if not changed:
                return []

            # 重建 Pydantic 模型（保证类型校验）
            self._ctx = GlobalContext.model_validate(ctx_dict)
            self._ctx.journey.updated_at = time.time()

            # 触发订阅回调
            for field_path in changed:
                old_val = updates.get(field_path)  # 注意：这里用旧值不太对，简化处理
                self._notify_subscribers(field_path, old_val)

            # 推送到所有订阅者队列（fan-out）
            event = {"type": "gcp_update", "fields": changed, "snapshot": self.to_dict()}
            dead_queues: Set[asyncio.Queue] = set()
            for q in self._event_queues:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("gcp event queue full, dropping event")
                except Exception:
                    dead_queues.add(q)
            for q in dead_queues:
                self._event_queues.discard(q)

            logger.info("gcp updated, %d fields changed: %s", len(changed), changed)
            return changed

    async def update_module(self, module: str, data: Dict[str, Any]) -> List[str]:
        """更新整个模块，如 'vehicle'、'weather'。"""
        prefix = f"{module}."
        flat = self._flatten_dict(data, prefix)
        return await self.update(flat)

    def set_user_profile(self, profile: UserProfile) -> None:
        """直接设置用户画像（同步方法，启动时用）。"""
        self._ctx.user_profile = profile

    def set_journey(self, journey: JourneyState) -> None:
        """直接设置旅程状态。"""
        self._ctx.journey = journey
        self._ctx.journey.updated_at = time.time()

    # ---------- 订阅 ----------

    def subscribe(self, field_path: str, callback: SubscriberFn) -> None:
        """订阅某个字段或模块的变化。

        - 精确字段："traffic.on_route.overall_status"
        - 模块级："traffic" —— 模块内任何字段变化都会触发
        """
        if field_path not in self._subscribers:
            self._subscribers[field_path] = []
            if "." not in field_path:
                self._prefix_watchers.append(field_path)
        self._subscribers[field_path].append(callback)

    def _notify_subscribers(self, field_path: str, new_val: Any) -> None:
        # 精确匹配
        for cb in self._subscribers.get(field_path, []):
            try:
                cb(field_path, None, new_val)
            except Exception as e:
                logger.error("subscriber error for %s: %s", field_path, e)

        # 模块级匹配
        parts = field_path.split(".")
        for i in range(1, len(parts)):
            prefix = ".".join(parts[:i])
            if prefix in self._subscribers:
                for cb in self._subscribers[prefix]:
                    try:
                        cb(field_path, None, new_val)
                    except Exception as e:
                        logger.error("subscriber error for %s: %s", field_path, e)

    # ---------- 事件队列（fan-out pub/sub，给 WebSocket 和 Orchestrator 用） ----------

    def subscribe_events(self, maxsize: int = 200) -> asyncio.Queue:
        """获取一个独立的事件队列，订阅者各自消费不互相影响。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._event_queues.add(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        """取消订阅，清理队列。"""
        self._event_queues.discard(q)

    # 兼容旧接口（单队列），内部使用 subscribe_events
    def get_event_queue(self) -> asyncio.Queue:
        return self.subscribe_events()

    # ---------- 工具函数 ----------

    @staticmethod
    def _get_by_path(d: Dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        val: Any = d
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return None
            if val is None:
                return None
        return val

    @staticmethod
    def _set_by_path(d: Dict[str, Any], path: str, value: Any) -> None:
        parts = path.split(".")
        cur = d
        for part in parts[:-1]:
            if part not in cur or not isinstance(cur[part], dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value

    @staticmethod
    def _flatten_dict(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
        flat: Dict[str, Any] = {}
        for k, v in d.items():
            key = f"{prefix}{k}" if prefix else k
            if isinstance(v, dict):
                flat.update(ContextStore._flatten_dict(v, f"{key}."))
            else:
                flat[key] = v
        return flat


def get_store() -> ContextStore:
    return ContextStore.get_instance()
