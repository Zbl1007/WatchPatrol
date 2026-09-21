"""
课时费管理系统 · 每日运营数据汇报插件
继承自 BasePlugin，定时（或手动）请求系统内部指标统计接口，
汇总当日新增用户、AI使用量、记课流水、活跃人数及开通订阅人数，并推送微信模板消息。
"""

import os
import logging
from typing import Dict, Any
from datetime import datetime
try:
    import pytz
    CHINA_TZ = pytz.timezone("Asia/Shanghai")
except ImportError:
    try:
        from zoneinfo import ZoneInfo
        CHINA_TZ = ZoneInfo("Asia/Shanghai")
    except ImportError:
        from datetime import timezone, timedelta
        CHINA_TZ = timezone(timedelta(hours=8))

import httpx

from core.plugin_base import BasePlugin
from core.snapshot_manager import snapshot_manager
from notifier import send_job_alert

logger = logging.getLogger("plugins.teach_fee")


class TeachFeePlugin(BasePlugin):
    """课时费系统每日运营数据汇总插件"""

    plugin_id = "teach_fee"
    plugin_name = "课时费系统·运营日报"
    description = "每日固定时间（默认每晚22:00）从课时费管理平台拉取当日新增用户、AI使用量、记课流水及订阅充值等核心指标，直推微信大屏卡片。"
    author = "系统内置"
    version = "2.0.0"
    supported_types = ["scheduled"]
    default_type = "scheduled"
    default_report_time = "22:00"

    config_fields = [
        {
            "key": "api_url",
            "label": "指标接口地址",
            "type": "text",
            "default": "http://127.0.0.1:8000/api/v1/internal/daily-metrics",
            "placeholder": "http://...",
        },
        {
            "key": "api_key",
            "label": "接口通信密钥",
            "type": "text",
            "default": "watchpatrol_metrics_secret_2026",
            "placeholder": "请输入X-API-KEY",
        },
    ]

    async def fetch_metrics(self, api_url: str, api_key: str) -> Dict[str, Any]:
        headers = {
            "X-API-KEY": api_key,
            "User-Agent": "WatchPatrol-Plugin/2.0",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code == 401:
                raise RuntimeError("请求课时费系统失败: 密钥无效或未授权 (401 Unauthorized)")
            elif resp.status_code == 503:
                raise RuntimeError("请求课时费系统失败: 服务端尚未配置 MONITOR_METRICS_KEY (503)")
            resp.raise_for_status()
            res_json = resp.json()

        if res_json.get("code") != 200:
            raise RuntimeError(f"接口返回业务错误: {res_json.get('message')}")

        return res_json.get("data", {})

    async def send_report_notification(
        self, task_id: str, metrics: Dict[str, Any], date_str: str, notify_openids: list
    ) -> bool:
        if not notify_openids:
            return False

        new_users = metrics.get("new_users_today", 0)
        ai_usage = metrics.get("ai_usage_today", 0)
        ai_breakdown = metrics.get("ai_breakdown", {})
        fb_gen = ai_breakdown.get("feedback_generation", 0)
        chat_cnt = ai_breakdown.get("agent_chat", 0)
        class_records = metrics.get("new_class_records_today", 0)
        active_users = metrics.get("active_users_today", 0)
        new_subs = metrics.get("new_subscriptions_today", 0)
        sub_income = metrics.get("subscription_income_today", 0.0)
        total_users = metrics.get("total_users", 0)

        enroll_status_text = (
            f"新增 {new_users} 人 | AI调用 {ai_usage} 次 | 订阅 {new_subs} 人 (¥{sub_income})"
        )

        remark_text = (
            f"📅 统计日期: {date_str}\n"
            f"👥 当日新增: {new_users} 人 (总用户数: {total_users})\n"
            f"🤖 AI使用量: {ai_usage} 次 (反馈 {fb_gen} / 对话 {chat_cnt})\n"
            f"📝 新增课时: {class_records} 条\n"
            f"🔥 活跃人数: {active_users} 人\n"
            f"💎 开通订阅: {new_subs} 人 (充值流水: ¥{sub_income})"
        )

        snap_data = dict(metrics)
        snap_data["date"] = date_str
        snap_id = snapshot_manager.create_snapshot(
            task_id=task_id,
            title=f"课时费运营日报 ({date_str})",
            data=snap_data,
            ttl_hours=24,
        )
        click_url = snapshot_manager.get_snapshot_url(snap_id)
        custom_tpl = os.getenv("TEACH_FEE_TEMPLATE_ID")

        res = await send_job_alert(
            unit="课时费计算管理平台",
            post="每日核心运营指标日报",
            enroll_status=enroll_status_text,
            remark=remark_text,
            title=f"【每日日报】课时费系统 {date_str} 运营数据汇总",
            to=notify_openids,
            click_url=click_url,
            template_id=custom_tpl,
        )
        return res.success

    async def run(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        task_id = context.get("task_id", "teach_fee")
        state = context.get("state", {})
        force = context.get("force", False)
        notify_openids = context.get("notify_openids", [])
        save_state = context.get("save_state")
        record_history = context.get("record_history")

        now = datetime.now(CHINA_TZ)
        today_str = now.strftime("%Y-%m-%d")

        api_url = config.get("api_url") or os.getenv(
            "TEACH_FEE_API_URL", "http://127.0.0.1:8000/api/v1/internal/daily-metrics"
        ).strip()
        api_key = config.get("api_key") or os.getenv(
            "TEACH_FEE_API_KEY", "watchpatrol_metrics_secret_2026"
        ).strip()

        metrics = await self.fetch_metrics(api_url, api_key)
        notified = await self.send_report_notification(task_id, metrics, today_str, notify_openids)

        state["last_metrics"] = metrics
        state["last_check_time"] = now.strftime("%Y-%m-%d %H:%M:%S")

        record = {
            "date": today_str,
            "metrics": metrics,
            "notified": notified,
            "report_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_type": "manual" if force else "scheduled",
        }
        if callable(record_history):
            record_history(record)
        if callable(save_state):
            save_state()

        return {
            "status": "reported",
            "trigger_type": "manual" if force else "scheduled",
            "date": today_str,
            "metrics": metrics,
            "notified": notified,
        }
