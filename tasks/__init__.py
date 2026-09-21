"""
监控任务集
统一注册与导出系统内的所有监控任务
"""

from core.task_manager import task_manager
from .jd_offer_task import JdOfferMonitorTask
from .example_task import ExampleMonitorTask

# 实例化并注册核心任务
jd_offer_task = JdOfferMonitorTask()
task_manager.register(jd_offer_task)

# 注册示例任务（默认处于禁用状态）
example_task = ExampleMonitorTask()
task_manager.register(example_task)

__all__ = [
    "jd_offer_task",
    "example_task",
]
