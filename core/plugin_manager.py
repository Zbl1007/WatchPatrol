"""
脚本插件扫描与管理中心模块
负责扫描 plugins/ 目录下的所有插件文件，动态导入并解析其元数据。
支持热重载，便于维护人员放置新脚本后立即被 Web 控制台发现。
"""

import os
import sys
import inspect
import logging
import importlib.util
from pathlib import Path
from typing import Dict, List, Optional, Type
from .plugin_base import BasePlugin

logger = logging.getLogger("core.plugin")


class PluginManager:
    """插件管理器（单例）"""

    def __init__(self, plugins_dir: Optional[Path] = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.plugins_dir = plugins_dir or (base_dir / "plugins")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.plugins: Dict[str, BasePlugin] = {}
        self.reload()

    def reload(self):
        """重新扫描 plugins/ 目录并载入所有可用脚本插件"""
        logger.info("--> 正在扫描插件目录: %s", self.plugins_dir)
        self.plugins.clear()

        # 遍历目录中的所有 .py 文件 (忽略 __init__.py 等以 _ 开头的文件)
        for entry in self.plugins_dir.glob("*.py"):
            if entry.name.startswith("_"):
                continue

            try:
                module_name = f"plugins.{entry.stem}"
                spec = importlib.util.spec_from_file_location(module_name, entry)
                if spec is None or spec.loader is None:
                    continue

                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                # 寻找该模块中所有继承自 BasePlugin 的具体类
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BasePlugin)
                        and attr is not BasePlugin
                        and not inspect.isabstract(attr)
                    ):
                        instance = attr()
                        self.plugins[instance.plugin_id] = instance
                        logger.info(
                            "成功加载插件: [%s] %s v%s (作者: %s)",
                            instance.plugin_id,
                            instance.plugin_name,
                            instance.version,
                            instance.author,
                        )

            except Exception as e:
                logger.error("加载插件文件 [%s] 失败: %s", entry.name, e, exc_info=True)

        logger.info("插件扫描完成，当前已加载 %d 个插件", len(self.plugins))

    def get_plugin(self, plugin_id: str) -> Optional[BasePlugin]:
        """获取指定 ID 的插件实例"""
        return self.plugins.get(plugin_id)

    def list_plugins(self) -> List[Dict]:
        """获取所有可用插件的元数据列表（供前端下拉框渲染）"""
        return [p.get_meta() for p in self.plugins.values()]


# 全局插件管理器单例
plugin_manager = PluginManager()
