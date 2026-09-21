"""
通用监控脚本插件基类规范
所有放置在 plugins/ 目录下的自定义业务监控脚本必须继承此类。
开发者只需关注业务数据抓取、对比和微信通知，调度平台自动处理生命周期与状态持久化。
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional


class BasePlugin(ABC):
    """
    监控脚本插件基类
    """

    # 插件唯一标识符 (全小写英文、下划线)
    plugin_id: str = "base_plugin"

    # 插件人类可读中文名称 (供前端下拉框展示)
    plugin_name: str = "基础插件模板"

    # 插件功能介绍与使用说明
    description: str = "插件基础模板，请在子类中重写功能描述"

    # 插件作者与维护人员信息
    author: str = "系统管理员"

    # 插件版本
    version: str = "1.0.0"

    # 支持的调度类型: 'interval' (周期巡检), 'scheduled' (每日定时)
    supported_types: List[str] = ["interval"]

    # 默认调度类型
    default_type: str = "interval"

    # 默认巡检间隔 (秒，当类型为 interval 时生效)
    default_interval: int = 60

    # 默认推送时间 (HH:MM，当类型为 scheduled 时生效)
    default_report_time: str = "22:00"

    # 插件专属参数定义 (可选，用于前端动态渲染表单输入项)
    # 格式示例:
    # [
    #   {"key": "target_url", "label": "监控目标地址", "type": "text", "default": "", "placeholder": "https://..."},
    #   {"key": "threshold", "label": "告警阈值", "type": "number", "default": 100}
    # ]
    config_fields: List[Dict[str, Any]] = []

    @classmethod
    def get_meta(cls) -> Dict[str, Any]:
        """获取插件元数据字典（供前端下拉框与配置表单使用）"""
        return {
            "plugin_id": cls.plugin_id,
            "plugin_name": cls.plugin_name,
            "description": cls.description,
            "author": cls.author,
            "version": cls.version,
            "supported_types": cls.supported_types,
            "default_type": cls.default_type,
            "default_interval": cls.default_interval,
            "default_report_time": cls.default_report_time,
            "config_fields": cls.config_fields,
        }

    @abstractmethod
    async def run(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """
        插件核心检测业务逻辑入口
        
        :param context: 运行上下文，包含:
            - task_id (str): 当前运行的任务实例 ID
            - task_name (str): 任务实例名称
            - force (bool): 是否为手动强制触发
            - notify_openids (List[str]): 已解析的目标微信 openid 列表
            - state (dict): 当前任务持久化的私有状态字典（可直接读写）
            - save_state (callable): 保存当前任务状态的方法
            - record_history (callable): 向当前任务历史流水追加记录的方法
        :param config: 用户在 Web 端为该任务实例定制的专属参数 (由 config_fields 定义)
        
        :return: 业务检查结果摘要字典 (必须可 JSON 序列化)
        """
        pass
