"""
任务调度管理中心模块
负责所有监控任务的注册、独立协程调度、生命周期管控与聚合上报。
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from .base_task import BaseTask

logger = logging.getLogger("core.manager")


class TaskManager:
    """多任务调度管理器"""

    def __init__(self):
        self.tasks: Dict[str, BaseTask] = {}
        self._loop_coroutines: Dict[str, asyncio.Task] = {}
        self.is_running: bool = False

    def register(self, task: BaseTask) -> BaseTask:
        """注册一个监控任务"""
        if task.task_id in self.tasks:
            logger.warning("任务 ID 重复注册，将覆盖旧实例: %s", task.task_id)
        self.tasks[task.task_id] = task
        logger.info("监控任务注册成功: [%s] %s (周期: %ds)", task.task_id, task.task_name, task.interval_seconds)
        return task

    def get_task(self, task_id: str) -> Optional[BaseTask]:
        """获取指定任务实例"""
        return self.tasks.get(task_id)

    def get_all_summaries(self) -> List[Dict[str, Any]]:
        """获取全部任务的运行健康状态与配置列表"""
        return [task.get_summary() for task in self.tasks.values()]

    async def _task_worker(self, task: BaseTask):
        """单个任务的独立守护轮询工作协程（相互隔离，互不阻塞）"""
        logger.info("[%s] 独立监控协程已启动，执行周期: 每 %d 秒一次", task.task_id, task.interval_seconds)

        while self.is_running and task.enabled:
            try:
                await task.run_once()
            except Exception as e:
                logger.error("[%s] 任务工作流未捕获异常: %s", task.task_id, e)

            # 等待设定的间隔时间（支持动态响应退出）
            try:
                await asyncio.sleep(task.interval_seconds)
            except asyncio.CancelledError:
                break

        logger.info("[%s] 独立监控协程已退出", task.task_id)

    async def start_all(self):
        """启动所有已启用任务的后台轮询调度"""
        if self.is_running:
            logger.warning("任务调度器已经在运行中")
            return

        self.is_running = True
        logger.info("--> 正在启动任务调度中心，注册任务数: %d", len(self.tasks))

        for task_id, task in self.tasks.items():
            if task.enabled:
                coro = asyncio.create_task(self._task_worker(task))
                self._loop_coroutines[task_id] = coro

    def stop_all(self):
        """停止所有任务调度"""
        if not self.is_running:
            return

        self.is_running = False
        logger.info("<-- 正在停止任务调度中心...")

        for task_id, coro in list(self._loop_coroutines.items()):
            if not coro.done():
                coro.cancel()

        self._loop_coroutines.clear()
        logger.info("所有监控任务均已安全终止。")

    async def trigger_task(self, task_id: str) -> Dict[str, Any]:
        """手动立即执行指定任务一次"""
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"未找到任务 ID: {task_id}")
        return await task.run_once()


# 全局任务调度中心单例
task_manager = TaskManager()
