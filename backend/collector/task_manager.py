"""
采集任务管理器
负责创建、调度和追踪后台采集任务
"""

import asyncio
import logging
from typing import Dict, List, Optional

from backend.database.db import (
    create_collection_task,
    update_task_status,
    get_task,
    get_all_tasks,
)

logger = logging.getLogger("ScholarQ.TaskManager")


class CollectionTaskManager:
    """采集任务管理器 — 单例模式，管理后台采集任务"""

    def __init__(self):
        self._running_tasks: Dict[int, asyncio.Task] = {}

    async def submit_collection(
        self, query: str, sources: List[str], limit: int = 10
    ) -> List[dict]:
        """
        提交采集任务。为每个 source 创建独立的任务。
        
        Returns:
            创建的任务列表 [{"task_id": int, "source": str}, ...]
        """
        created_tasks = []

        for source in sources:
            source = source.lower().strip()
            if source not in ("s2", "epo", "uspto"):
                logger.warning(f"Unknown source: {source}, skipping")
                continue

            task_id = await create_collection_task(query, source)
            logger.info(f"[TaskManager] Created task #{task_id} for source={source}, query='{query}'")

            # 启动后台协程
            bg_task = asyncio.create_task(
                self._run_collection(task_id, query, source, limit)
            )
            self._running_tasks[task_id] = bg_task

            created_tasks.append({"task_id": task_id, "source": source})

        return created_tasks

    async def _run_collection(
        self, task_id: int, query: str, source: str, limit: int
    ):
        """执行单个采集任务"""
        try:
            await update_task_status(task_id, status="running")

            async def on_progress(collected: int, total: int):
                await update_task_status(
                    task_id, total_found=total, collected_count=collected
                )

            if source == "s2":
                from backend.collector.s2_collector import collect_papers
                result = await collect_papers(query, limit=limit, on_progress=on_progress)
            elif source == "epo":
                from backend.collector.epo_collector import collect_patents
                # 先构建英文查询
                en_query = await self._build_query(query)
                result = await collect_patents(en_query, limit=limit, on_progress=on_progress)
            elif source == "uspto":
                from backend.collector.uspto_collector import collect_patents
                result = await collect_patents(query, limit=limit, on_progress=on_progress)
            else:
                raise ValueError(f"Unknown source: {source}")

            await update_task_status(
                task_id,
                status="done",
                total_found=result.get("total_found", 0),
                collected_count=result.get("collected", 0),
                finished=True,
            )
            logger.info(f"[TaskManager] Task #{task_id} ({source}) completed: {result}")

        except Exception as e:
            logger.error(f"[TaskManager] Task #{task_id} ({source}) failed: {e}")
            await update_task_status(
                task_id,
                status="failed",
                error_msg=str(e),
                finished=True,
            )
        finally:
            self._running_tasks.pop(task_id, None)

    async def _build_query(self, query: str) -> str:
        """使用 LLM 将用户查询转换为英文专利检索式"""
        try:
            from backend.agent import build_patent_query
            return await build_patent_query(query)
        except Exception as e:
            logger.warning(f"[TaskManager] Query translation failed: {e}, using raw query")
            return query

    async def get_task_status(self, task_id: int) -> Optional[dict]:
        """查询单个任务状态"""
        return await get_task(task_id)

    async def get_all_task_statuses(self, limit: int = 50) -> List[dict]:
        """查询所有任务"""
        return await get_all_tasks(limit)
