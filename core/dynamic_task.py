"""
动态任务实例包装类模块
基于选定的插件模板，实例化为一个具备独立生命周期、独立状态持久化与调度协程的监控任务。
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
import pytz

from .base_task import BaseTask
from .plugin_manager import plugin_manager
from .contact_manager import contact_manager

logger = logging.getLogger("core.dynamic_task")
CHINA_TZ = pytz.timezone("Asia/Shanghai")


class DynamicTask(BaseTask):
    """
    可动态实例化的监控任务
    将用户配置（调度类型、时间、通知人员、参数）与插件业务逻辑进行绑定执行。
    """

    def __init__(
        self,
        task_id: str,
        task_name: str,
        plugin_id: str,
        task_type: str = "interval",
        interval_seconds: int = 60,
        report_time: str = "22:00",
        enabled: bool = True,
        enable_notify: bool = True,
        notify_contact_ids: Optional[List[str]] = None,
        plugin_config: Optional[Dict[str, Any]] = None,
        is_builtin: bool = False,
    ):
        self.task_id = task_id
        self.task_name = task_name
        self.plugin_id = plugin_id
        self.task_type = task_type
        self.interval_seconds = interval_seconds
        self.report_time = report_time
        self.enabled = enabled
        self.enable_notify = enable_notify
        self.notify_contact_ids = notify_contact_ids or []
        self.plugin_config = plugin_config or {}
        self.is_builtin = is_builtin

        super().__init__()

        # 同步恢复持久化配置中的值（若存在）
        saved_cfg = self.state.get("config", {})
        if "task_name" in saved_cfg:
            self.task_name = saved_cfg["task_name"]
        if "plugin_id" in saved_cfg:
            self.plugin_id = saved_cfg["plugin_id"]
        if "task_type" in saved_cfg:
            self.task_type = saved_cfg["task_type"]
        if "interval_seconds" in saved_cfg:
            self.interval_seconds = int(saved_cfg["interval_seconds"])
        if "report_time" in saved_cfg:
            self.report_time = saved_cfg["report_time"]
        if "enabled" in saved_cfg:
            self.enabled = bool(saved_cfg["enabled"])
        if "enable_notify" in saved_cfg:
            self.enable_notify = bool(saved_cfg["enable_notify"])
        if "notify_contact_ids" in saved_cfg:
            self.notify_contact_ids = saved_cfg["notify_contact_ids"]
        if "plugin_config" in saved_cfg:
            self.plugin_config = saved_cfg["plugin_config"]

    def get_plugin(self):
        """获取当前任务绑定的插件实例"""
        return plugin_manager.get_plugin(self.plugin_id)

    def get_notify_openids(self) -> list:
        """解析当前任务配置的联系人 OpenID 列表"""
        if not self.enable_notify:
            return []
        if self.notify_contact_ids:
            return contact_manager.resolve_openids(self.notify_contact_ids)
        # 若未指定特定联系人，回退至全局管理员
        return super().get_notify_openids()

    def get_contact_names(self) -> List[str]:
        """获取当前配置的接收人姓名展示标签"""
        if not self.enable_notify:
            return ["微信通知已关闭"]
        if self.notify_contact_ids:
            return contact_manager.get_contact_names(self.notify_contact_ids)
        return ["全局默认管理员"]

    def get_summary(self) -> Dict[str, Any]:
        """扩展摘要信息，带上插件元数据与联系人名称"""
        res = super().get_summary()
        res["task_type"] = self.task_type
        res["report_time"] = self.report_time
        res["plugin_id"] = self.plugin_id
        plugin = self.get_plugin()
        res["plugin_name"] = plugin.plugin_name if plugin else "未知脚本"
        res["enable_notify"] = self.enable_notify
        res["notify_contact_ids"] = self.notify_contact_ids
        res["contact_names"] = self.get_contact_names()
        res["is_builtin"] = self.is_builtin
        return res

    def get_config(self) -> Dict[str, Any]:
        """获取完整配置详情"""
        res = super().get_config()
        res.update({
            "task_id": self.task_id,
            "task_name": self.task_name,
            "plugin_id": self.plugin_id,
            "task_type": self.task_type,
            "report_time": self.report_time,
            "enable_notify": self.enable_notify,
            "notify_contact_ids": self.notify_contact_ids,
            "plugin_config": self.plugin_config,
            "is_builtin": self.is_builtin,
        })
        return res

    def update_config(
        self,
        task_name: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        report_time: Optional[str] = None,
        enabled: Optional[bool] = None,
        enable_notify: Optional[bool] = None,
        notify_contact_ids: Optional[List[str]] = None,
        plugin_config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """在线更新任务配置并同步持久化"""
        if task_name is not None:
            self.task_name = str(task_name).strip() or self.task_name
        if interval_seconds is not None:
            self.interval_seconds = max(10, int(interval_seconds))
        if report_time is not None:
            self.report_time = str(report_time).strip() or self.report_time
        if enabled is not None:
            self.enabled = bool(enabled)
        if enable_notify is not None:
            self.enable_notify = bool(enable_notify)
        if notify_contact_ids is not None:
            self.notify_contact_ids = list(notify_contact_ids)
        if plugin_config is not None:
            self.plugin_config = dict(plugin_config)

        # 保存到状态文件的 config 字段中
        self.state["config"] = {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "plugin_id": self.plugin_id,
            "task_type": self.task_type,
            "interval_seconds": self.interval_seconds,
            "report_time": self.report_time,
            "enabled": self.enabled,
            "enable_notify": self.enable_notify,
            "notify_contact_ids": self.notify_contact_ids,
            "plugin_config": self.plugin_config,
            "is_builtin": self.is_builtin,
        }
        self.save_state()
        logger.info("[%s] 任务配置已热更新并持久化", self.task_id)
        return self.get_config()

    async def execute_check(self, force: bool = False) -> Dict[str, Any]:
        """
        统一检测生命周期：
        1. 定时任务严格防抖防护 (仅在到达时刻且今天未发，或 force=True 时执行)；
        2. 调度绑定的脚本插件并传入运行上下文。
        """
        plugin = self.get_plugin()
        if not plugin:
            raise RuntimeError(f"绑定的脚本插件不存在或未加载: {self.plugin_id}")

        now = datetime.now(CHINA_TZ)
        today_str = now.strftime("%Y-%m-%d")
        current_hm = now.strftime("%H:%M")

        # 若属于定时任务型 (scheduled)
        if self.task_type == "scheduled":
            target_time = self.report_time or "22:00"
            already_reported = (self.state.get("last_reported_date") == today_str)
            should_send = force or (current_hm >= target_time and not already_reported)

            if not should_send:
                return {
                    "status": "idle",
                    "current_time": current_hm,
                    "target_time": target_time,
                    "already_reported_today": already_reported,
                    "message": f"当前时间 {current_hm}，目标汇报时刻 {target_time}（{'今日已推送' if already_reported else '等待时间到达'}）",
                }

        # 准备执行上下文
        target_openids = self.get_notify_openids()
        context = {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "task_type": self.task_type,
            "force": force,
            "notify_openids": target_openids,
            "state": self.state,
            "save_state": self.save_state,
            "record_history": self.record_history,
        }

        # 执行插件核心检测逻辑
        result = await plugin.run(context=context, config=self.plugin_config)

        # 若是定时任务且满足自然报送，标记今日已发
        if self.task_type == "scheduled" and not force:
            self.state["last_reported_date"] = today_str
            self.save_state()

        return result
