"""
任务调度管理中心模块
负责所有静态/动态监控任务的注册、持久化管理、独立协程生命周期管控与聚合上报。
支持运行时动态新增任务、动态注销任务，即刻热生效无需重启服务。
"""

import os
import json
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from .base_task import BaseTask
from .dynamic_task import DynamicTask

logger = logging.getLogger("core.manager")


class TaskManager:
    """多任务调度管理器"""

    def __init__(self, data_dir: Optional[Path] = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.data_dir = data_dir or (base_dir / "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.data_dir / "dynamic_tasks.json"

        self.tasks: Dict[str, BaseTask] = {}
        self._loop_coroutines: Dict[str, asyncio.Task] = {}
        self.is_running: bool = False

    def register(self, task: BaseTask) -> BaseTask:
        """注册一个任务实例"""
        if task.task_id in self.tasks:
            logger.warning("任务 ID 重复注册，将覆盖旧实例: %s", task.task_id)
        self.tasks[task.task_id] = task
        logger.info("监控任务注册成功: [%s] %s (周期: %ds)", task.task_id, task.task_name, task.interval_seconds)

        # 若调度中心已在运行且任务已启用，即刻挂载执行协程
        if self.is_running and task.enabled:
            self._ensure_worker_running(task)

        return task

    def get_task(self, task_id: str) -> Optional[BaseTask]:
        """获取指定任务实例"""
        return self.tasks.get(task_id)

    def get_all_summaries(self) -> List[Dict[str, Any]]:
        """获取全部任务的运行健康状态与配置列表"""
        return [task.get_summary() for task in self.tasks.values()]

    def _ensure_worker_running(self, task: BaseTask):
        """确保该任务的独立监控工作协程正在运行"""
        existing = self._loop_coroutines.get(task.task_id)
        if existing and not existing.done():
            return
        coro = asyncio.create_task(self._task_worker(task))
        self._loop_coroutines[task.task_id] = coro

    def _stop_worker(self, task_id: str):
        """安全停止并注销某个任务的独立工作协程"""
        coro = self._loop_coroutines.pop(task_id, None)
        if coro and not coro.done():
            coro.cancel()

    async def _task_worker(self, task: BaseTask):
        """单个任务的独立守护轮询工作协程（相互隔离，互不阻塞）"""
        logger.info("[%s] 独立监控协程已启动，执行周期: 每 %d 秒一次", task.task_id, task.interval_seconds)

        while self.is_running and task.enabled:
            try:
                # 默认后台定时/心跳巡检，force=False
                await task.run_once(force=False)
            except Exception as e:
                logger.error("[%s] 任务工作流未捕获异常: %s", task.task_id, e)

            # 等待设定的间隔时间（支持动态响应退出与间隔热变动）
            try:
                await asyncio.sleep(task.interval_seconds)
            except asyncio.CancelledError:
                break

        logger.info("[%s] 独立监控协程已退出", task.task_id)

    def load_dynamic_tasks(self):
        """从持久化文件载入用户动态创建的任务"""
        if not self.registry_file.exists():
            return

        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                records = json.load(f)
            for item in records:
                task_id = item.get("task_id")
                if not task_id or task_id in self.tasks:
                    continue
                d_task = DynamicTask(
                    task_id=task_id,
                    task_name=item.get("task_name", "自定义任务"),
                    plugin_id=item.get("plugin_id", ""),
                    task_type=item.get("task_type", "interval"),
                    interval_seconds=item.get("interval_seconds", 60),
                    report_time=item.get("report_time", "22:00"),
                    enabled=item.get("enabled", True),
                    enable_notify=item.get("enable_notify", True),
                    notify_contact_ids=item.get("notify_contact_ids", []),
                    plugin_config=item.get("plugin_config", {}),
                    is_builtin=False,
                )
                self.register(d_task)
            logger.info("已成功恢复 %d 个自定义动态任务", len(records))
        except Exception as e:
            logger.error("恢复动态任务失败: %s", e)

    def _save_dynamic_tasks(self):
        """原子化保存所有自定义动态任务"""
        records = []
        for task in self.tasks.values():
            if isinstance(task, DynamicTask) and not getattr(task, "is_builtin", False):
                records.append({
                    "task_id": task.task_id,
                    "task_name": task.task_name,
                    "plugin_id": task.plugin_id,
                    "task_type": task.task_type,
                    "interval_seconds": task.interval_seconds,
                    "report_time": task.report_time,
                    "enabled": task.enabled,
                    "enable_notify": task.enable_notify,
                    "notify_contact_ids": task.notify_contact_ids,
                    "plugin_config": task.plugin_config,
                })

        temp_file = self.registry_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            temp_file.replace(self.registry_file)
        except Exception as e:
            logger.error("保存动态任务注册表失败: %s", e)

    def create_task(
        self,
        task_name: str,
        plugin_id: str,
        task_type: str = "interval",
        interval_seconds: int = 60,
        report_time: str = "22:00",
        enabled: bool = True,
        enable_notify: bool = True,
        notify_contact_ids: Optional[List[str]] = None,
        plugin_config: Optional[Dict[str, Any]] = None,
    ) -> DynamicTask:
        """动态创建新监控任务"""
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        new_task = DynamicTask(
            task_id=task_id,
            task_name=task_name.strip() or "新建监控任务",
            plugin_id=plugin_id,
            task_type=task_type,
            interval_seconds=max(10, int(interval_seconds)),
            report_time=report_time.strip() or "22:00",
            enabled=enabled,
            enable_notify=enable_notify,
            notify_contact_ids=notify_contact_ids or [],
            plugin_config=plugin_config or {},
            is_builtin=False,
        )

        self.register(new_task)
        self._save_dynamic_tasks()
        logger.info("成功动态创建监控任务: [%s] %s", task_id, new_task.task_name)
        return new_task

    def delete_task(self, task_id: str) -> bool:
        """动态删除指定任务（内置任务不可删除）"""
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"任务不存在: {task_id}")

        if getattr(task, "is_builtin", False):
            raise ValueError("系统内置任务不可删除，仅支持停用或修改配置")

        # 停止后台协程
        self._stop_worker(task_id)

        # 移除实例
        del self.tasks[task_id]
        self._save_dynamic_tasks()

        # 清理状态文件
        try:
            if task.state_file.exists():
                task.state_file.unlink()
        except Exception:
            pass

        logger.info("成功删除动态任务: %s", task_id)
        return True

    def sync_task_worker(self, task_id: str):
        """根据任务的 enabled 状态同步协程状态"""
        task = self.get_task(task_id)
        if not task:
            return
        if self.is_running and task.enabled:
            self._ensure_worker_running(task)
        else:
            self._stop_worker(task_id)

    async def start_all(self):
        """启动所有已启用任务的后台轮询调度"""
        if self.is_running:
            logger.warning("任务调度器已经在运行中")
            return

        self.is_running = True
        # 载入用户持久化的动态任务
        self.load_dynamic_tasks()

        logger.info("--> 正在启动任务调度中心，注册任务数: %d", len(self.tasks))

        for task in self.tasks.values():
            if task.enabled:
                self._ensure_worker_running(task)

    def stop_all(self):
        """停止所有任务调度"""
        if not self.is_running:
            return

        self.is_running = False
        logger.info("<-- 正在停止任务调度中心...")

        for task_id in list(self._loop_coroutines.keys()):
            self._stop_worker(task_id)

        self._loop_coroutines.clear()
        logger.info("所有监控任务均已安全终止。")

    async def trigger_task(self, task_id: str, force: bool = True) -> Dict[str, Any]:
        """手动立即执行指定任务一次 (force=True 时强制执行不检查时刻)"""
        task = self.get_task(task_id)
        if not task:
            raise KeyError(f"未找到任务 ID: {task_id}")
        return await task.run_once(force=force)


# 全局任务调度中心单例
task_manager = TaskManager()
