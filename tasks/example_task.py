"""
示例监控任务模板 (供后续新增监控时复制参考)
如需添加新监控任务，只需在 tasks/ 目录下创建新 .py 文件并继承 BaseTask。
"""

import logging
from typing import Dict, Any
from core.base_task import BaseTask
from notifier import send_wechat_notice

logger = logging.getLogger("tasks.example")


class ExampleMonitorTask(BaseTask):
    """
    自定义业务监控任务示例
    说明：修改类名、task_id、task_name 与 execute_check 中的业务逻辑即可。
    """

    task_id = "example_demo_monitor"            # 唯一任务 ID（用于状态文件命名与路由标识）
    task_name = "示例自定义监控任务"              # 任务友好展示名称
    interval_seconds = 300                      # 定时巡检周期（秒），例如 300 秒（5分钟）
    enabled = False                             # 是否默认启用（设为 True 时随系统自启）

    # 🌟 专属接收人：指定接收该任务变动提醒的微信 openid（留空/设为 None 则自动继承 .env 中的默认接收人）
    notify_openids = None

    async def execute_check(self) -> Dict[str, Any]:
        """
        核心检测业务逻辑
        """
        logger.info("[%s] 正在执行自定义检测逻辑...", self.task_id)

        current_value = 100
        old_value = self.state.get("last_value", 100)

        # 2. 判断变动
        has_changed = (current_value != old_value)

        if has_changed:
            logger.warning("[%s] 发现指标变化: %s -> %s", self.task_id, old_value, current_value)

            # 3. 触发微信推送（精确推送给本任务绑定的接收人列表）
            push_result = await send_wechat_notice(
                title=f"【监控告警】{self.task_name}触发变动",
                content=f"指标由 {old_value} 变更为 {current_value}",
                event_type="业务告警",
                remark="请进入后台查看",
                to=self.get_notify_openids(),  # 传入本任务的专属接收人
            )

            # 4. 追加变动流水记录（自动落盘至 data/{task_id}_state.json）
            self.record_history({
                "old_val": old_value,
                "new_val": current_value,
                "notified": push_result.success,
            })

            # 更新基准
            self.state["last_value"] = current_value
            self.save_state()

        return {
            "status": "ok",
            "has_changed": has_changed,
            "current_value": current_value,
        }
