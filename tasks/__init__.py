"""
监控任务插件包（自动扫描注册中心）
启动时全自动扫描当前 tasks/ 目录下的所有 .py 脚本，自动实例化并注册所有 BaseTask 任务。
零硬编码，零维护成本。
"""

import inspect
import logging
import importlib
from pathlib import Path
from typing import Dict, Any, List

from core.base_task import BaseTask
from core.task_manager import task_manager

logger = logging.getLogger("tasks.loader")

# 记录已自动载入的任务映射表 {task_id: BaseTask}
discovered_tasks: Dict[str, BaseTask] = {}


def auto_discover_and_register() -> Dict[str, BaseTask]:
    """
    扫描 tasks 目录下的所有 .py 文件，自动发现并注册 BaseTask 子类。
    支持自动识别已实例化的对象，或自动实例化未实例化的具体任务类。
    """
    current_dir = Path(__file__).resolve().parent

    for file_path in sorted(current_dir.glob("*.py")):
        module_name = file_path.stem
        # 忽略以 _ 开头的文件（如 __init__.py）
        if module_name.startswith("_"):
            continue

        try:
            module = importlib.import_module(f"tasks.{module_name}")
        except Exception as e:
            logger.error("自动加载任务模块 tasks.%s 异常: %s", module_name, e, exc_info=True)
            continue

        found_instances: List[BaseTask] = []

        # 1. 优先寻找模块内显式导出的 BaseTask 实例
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            attr = getattr(module, attr_name)
            if isinstance(attr, BaseTask) and attr not in found_instances:
                found_instances.append(attr)

        # 2. 若未显式实例化，自动发现继承自 BaseTask 的具体任务类并自动实例化
        if not found_instances:
            for attr_name, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, BaseTask) and cls is not BaseTask:
                    # 避免误实例化抽象基类
                    if getattr(cls, "task_id", "base_task") != "base_task":
                        try:
                            instance = cls()
                            found_instances.append(instance)
                        except Exception as e:
                            logger.error("自动实例化任务类 %s.%s 失败: %s", module_name, cls.__name__, e)

        # 3. 注册到 task_manager
        for task in found_instances:
            if not task_manager.get_task(task.task_id):
                task_manager.register(task)
                discovered_tasks[task.task_id] = task
                logger.info("[自动发现任务] 成功载入插件: [%s] %s (来自 tasks/%s.py)", task.task_id, task.task_name, module_name)
            else:
                discovered_tasks[task.task_id] = task_manager.get_task(task.task_id)

    return discovered_tasks


# 启动时执行纯动态自动扫描
auto_discover_and_register()


def __getattr__(name: str) -> Any:
    """动态模块属性查找，兼容通过任务ID或模块名直接获取任务实例，无需在代码中写死任何具体任务"""
    if name in discovered_tasks:
        return discovered_tasks[name]
    task = task_manager.get_task(name)
    if task:
        return task
    raise AttributeError(f"module 'tasks' has no attribute '{name}'")


__all__ = [
    "auto_discover_and_register",
    "discovered_tasks",
]
