"""
核心框架包
导出 BaseTask 与 task_manager
"""

from .base_task import BaseTask
from .task_manager import TaskManager, task_manager

__all__ = [
    "BaseTask",
    "TaskManager",
    "task_manager",
]
