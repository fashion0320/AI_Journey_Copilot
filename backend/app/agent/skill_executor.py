"""SkillExecutor — Skill 的串行/并行/DAG 执行器。

负责：
- 单个 Skill 执行（含 GCP 切片、事件推送、错误处理）
- 串行任务链执行
- 并行任务执行（asyncio.gather）
- DAG 任务计划执行（按依赖 + 并行组分波执行）
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from ..core.logging import get_logger
from ..skills import get_all_skills, get_skill, SkillResult, SkillStatus

logger = get_logger(__name__)


class SkillExecutor:
    """Skill 执行编排器。

    所有 Skill 调用统一通过此执行器，确保：
    - 正确的 GCP 切片
    - 事件推送（skill_start / skill_result）
    - 一致的错误处理
    - 串并行调度
    """

    def __init__(
        self,
        gcp_store: Any,
        stream_queue: Optional[asyncio.Queue] = None,
    ) -> None:
        self.gcp_store = gcp_store
        self.stream_queue = stream_queue
        self._skills = get_all_skills()

    # ---- 单个 Skill 执行 ----

    async def execute_skill(
        self,
        skill_name: str,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        task_id: str = "",
    ) -> Dict[str, Any]:
        """执行单个 Skill。

        Args:
            skill_name: Skill 名称（与 registry 中一致）
            action: Skill 内的 action/remind_type
            params: 调用参数（不含 action，会自动加进去）
            context: 额外上下文（如前序 Skill 结果）
            task_id: 任务 ID（用于事件推送标识）

        Returns:
            SkillResult.to_dict()
        """
        params = params or {}
        skill = self._skills.get(skill_name)

        if skill is None:
            err = f"skill '{skill_name}' not found"
            logger.error(err)
            await self._emit("skill_result", {
                "skill": skill_name, "action": action,
                "task_id": task_id, "status": "error", "error": err,
            })
            return SkillResult.error(err).to_dict()

        # 推送 SKILL_START
        await self._emit("skill_start", {
            "skill": skill_name, "action": action, "task_id": task_id,
        })

        # 取 GCP 切片
        try:
            gcp_dict = self.gcp_store.to_dict()
            gcp_slice = skill.extract_gcp_slice(gcp_dict)
        except Exception as e:
            logger.error("gcp slice error for %s: %s", skill_name, e)
            gcp_slice = {}

        # 执行
        try:
            full_params = {"action": action, **params}
            result = await skill.execute(full_params, gcp_slice, context)
            result_dict = result.to_dict()
        except Exception as e:
            logger.exception("skill %s action %s error: %s", skill_name, action, e)
            result = SkillResult.error(str(e))
            result_dict = result.to_dict()

        # 推送 SKILL_RESULT
        await self._emit("skill_result", {
            "skill": skill_name,
            "action": action,
            "task_id": task_id,
            "status": result_dict.get("status"),
            "result": result_dict,
        })

        return result_dict

    # ---- 并行执行 ----

    async def run_parallel(
        self,
        tasks: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """并行执行多个独立任务（asyncio.gather）。

        tasks: [{task_id, skill, action, params}]
        返回: {task_id: result_dict}
        """
        async def _run(task: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
            tid = task.get("task_id", "")
            result = await self.execute_skill(
                task["skill"], task["action"],
                task.get("params", {}), context, tid,
            )
            return tid, result

        results = await asyncio.gather(
            *[_run(t) for t in tasks], return_exceptions=True,
        )

        out: Dict[str, Dict[str, Any]] = {}
        for item in results:
            if isinstance(item, Exception):
                logger.error("parallel task exception: %s", item)
                continue
            tid, res = item
            out[tid] = res
        return out

    # ---- 串行执行 ----

    async def run_serial(
        self,
        tasks: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """串行执行任务链，后一个可以看到前一个的结果。

        context 中会追加 prev_{task_id} 键，存前一个任务的结果。
        """
        out: Dict[str, Dict[str, Any]] = {}
        ctx = dict(context or {})

        for task in tasks:
            tid = task.get("task_id", "")
            try:
                result = await self.execute_skill(
                    task["skill"], task["action"],
                    task.get("params", {}), ctx, tid,
                )
                out[tid] = result
                ctx[f"prev_{tid}"] = result
            except Exception as e:
                logger.error("serial task %s error: %s", tid, e)
                out[tid] = SkillResult.error(str(e)).to_dict()

        return out

    # ---- DAG 计划执行 ----

    async def execute_plan(
        self,
        plan: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """执行完整任务计划（DAG）。

        plan: [{task_id, skill, action, params, deps: [task_id], parallel_group}]

        执行策略（wave execution）：
        1. 找出所有依赖已满足的任务
        2. 按 parallel_group 分组，同组并行
        3. 完成后从剩余任务中移除，继续下一波
        4. 直到所有任务完成或发现循环依赖
        """
        completed: Dict[str, Dict[str, Any]] = {}
        remaining = list(plan)
        wave = 0
        max_waves = len(plan) * 2  # 防死循环

        while remaining and wave < max_waves:
            wave += 1

            # 找出所有依赖已满足的任务
            ready = [
                t for t in remaining
                if all(d in completed for d in t.get("deps", []))
            ]
            if not ready:
                logger.error("circular dependency or unmet deps in task plan: %s",
                             [t.get("task_id") for t in remaining])
                break

            # 按 parallel_group 分组
            groups: Dict[str, List[Dict[str, Any]]] = {}
            for t in ready:
                pg = t.get("parallel_group") or ""
                groups.setdefault(pg, []).append(t)

            ctx = {**(context or {}), "completed": completed}

            for pg, tasks in groups.items():
                if pg and len(tasks) > 1:
                    # 同组并行
                    results = await self.run_parallel(tasks, ctx)
                else:
                    # 无 parallel_group 或只有 1 个 — 串行（保持简单）
                    results = await self.run_serial(tasks, ctx)
                completed.update(results)

            # 从 remaining 中移除已完成的
            done_ids = {t["task_id"] for t in ready}
            remaining = [t for t in remaining if t.get("task_id") not in done_ids]

        if wave >= max_waves:
            logger.error("execute_plan reached max waves (%d), possible loop", max_waves)

        return completed

    # ---- 事件推送 ----

    async def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """推送事件到 stream_queue（如果有）。"""
        if self.stream_queue is None:
            return
        try:
            self.stream_queue.put_nowait((event_type, data))
        except asyncio.QueueFull:
            logger.warning("skill executor stream queue full, dropping %s", event_type)
