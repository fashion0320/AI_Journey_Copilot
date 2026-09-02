"""JourneyStore — 行程实例内存存储 + MemorySaver 包装。

MVP 阶段内存实现，后续可替换为 SQLite checkpointer。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from langgraph.checkpoint.memory import MemorySaver

from ..core.logging import get_logger

logger = get_logger(__name__)


class JourneyStore:
    """管理活跃旅程实例，包装 LangGraph MemorySaver。

    每个 WebSocket 连接对应一个旅程（MVP 阶段），journey_id 为连接标识。
    """

    _instance: Optional["JourneyStore"] = None

    def __init__(self) -> None:
        self._checkpointer = MemorySaver()
        self._journeys: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, Any]] = []  # 已完成行程历史
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "JourneyStore":
        if cls._instance is None:
            cls._instance = JourneyStore()
        return cls._instance

    def get_checkpointer(self) -> MemorySaver:
        return self._checkpointer

    def get_config(self, journey_id: str) -> Dict[str, Any]:
        """获取 LangGraph config（含 thread_id）。"""
        return {"configurable": {"thread_id": journey_id}}

    async def create_journey(self, journey_id: str) -> None:
        """创建新旅程实例。"""
        async with self._lock:
            self._journeys[journey_id] = {
                "created_at": time.time(),
                "last_activity": time.time(),
                "status": "active",
            }
        logger.info("journey created: %s", journey_id)

    async def touch(self, journey_id: str) -> None:
        """更新最后活跃时间。"""
        if journey_id in self._journeys:
            self._journeys[journey_id]["last_activity"] = time.time()

    async def complete_journey(
        self, journey_id: str, final_state: Dict[str, Any],
    ) -> None:
        """标记行程完成，保存摘要到历史。"""
        async with self._lock:
            if journey_id in self._journeys:
                self._journeys[journey_id]["status"] = "completed"
                self._journeys[journey_id]["completed_at"] = time.time()
                self._journeys[journey_id]["summary"] = {
                    "destination": final_state.get("destination_name", ""),
                    "intent_type": final_state.get("intent_type", ""),
                    "eta_min": (final_state.get("eta") or {}).get("remaining_min", 0),
                    "distance_km": (final_state.get("route") or {}).get("distance_km", 0),
                    "replan_count": final_state.get("replan_count", 0),
                }
                # 移到历史
                self._history.append({
                    **self._journeys[journey_id],
                    "journey_id": journey_id,
                })
                del self._journeys[journey_id]
        logger.info("journey completed: %s", journey_id)

    async def abandon_journey(self, journey_id: str) -> None:
        """连接断开时放弃行程。"""
        async with self._lock:
            if journey_id in self._journeys:
                self._journeys[journey_id]["status"] = "abandoned"
                del self._journeys[journey_id]
        logger.info("journey abandoned: %s", journey_id)

    async def update_preferences(
        self, user_id: str, journey_state: Dict[str, Any],
    ) -> None:
        """行程结束后增量更新用户偏好。

        MVP 简化实现：记录常用目的地（追加到 travel_preferences.frequent_pois）。
        """
        dest_name = journey_state.get("destination_name", "")
        dest = journey_state.get("destination") or {}
        if not dest_name or not dest:
            return

        from ..gcp.store import get_store
        store = get_store()
        ctx = store.snapshot()
        profile = ctx.user_profile

        # 检查是否已存在
        existing_names = {p.get("name") for p in profile.travel_preferences.frequent_pois}
        if dest_name in existing_names:
            return

        # 追加新目的地
        profile.travel_preferences.frequent_pois.append({
            "name": dest_name,
            "lat": dest.get("lat", 0),
            "lon": dest.get("lon", 0),
            "tag": "recent",
        })
        # 最多保留 20 条
        if len(profile.travel_preferences.frequent_pois) > 20:
            profile.travel_preferences.frequent_pois = \
                profile.travel_preferences.frequent_pois[-20:]

        store.set_user_profile(profile)

    def get_journey(self, journey_id: str) -> Optional[Dict[str, Any]]:
        return self._journeys.get(journey_id)

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取已完成行程历史（最近 N 条）。"""
        return self._history[-limit:]

    async def cleanup_stale(self, max_idle_sec: int = 3600) -> int:
        """清理空闲超时的旅程。返回清理数量。"""
        now = time.time()
        stale = []
        for jid, meta in self._journeys.items():
            if meta["status"] == "active" and now - meta["last_activity"] > max_idle_sec:
                stale.append(jid)
        async with self._lock:
            for jid in stale:
                self._journeys.pop(jid, None)
        if stale:
            logger.info("cleaned up %d stale journeys", len(stale))
        return len(stale)


def get_journey_store() -> JourneyStore:
    return JourneyStore.get_instance()
