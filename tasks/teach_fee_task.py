"""
课时费计算管理系统 · 每日运营数据汇报任务插件
继承自 BaseTask，每天固定时间（默认每晚 22:00）请求系统带密钥的指标统计接口，
汇总当日新增用户、AI使用量、记课流水、活跃人数及开通订阅人数，并直推微信模板消息。
"""

import os
import logging
from typing import Dict, Any, Optional
from datetime import datetime
import pytz
import httpx

from core.base_task import BaseTask
from core.snapshot_manager import snapshot_manager
from notifier import send_job_alert

logger = logging.getLogger("tasks.teach_fee")

CHINA_TZ = pytz.timezone("Asia/Shanghai")


class TeachFeeDailyReportTask(BaseTask):
    """课时费系统每日运营数据汇总任务"""

    task_id = "teach_fee_daily_report"
    task_name = "课时费系统·运营日报"
    task_type = "scheduled"  # 声明为定时任务型（每天定时汇总直推，无需配置周期巡检）
    interval_seconds = 60  # 后台每 60 秒轮询检查系统时刻是否到达
    enabled = True

    # 专属通知目标：若设为 None，系统将自动使用全局 WECHAT_OPENID
    # 也可在 Web 控制台点击「配置」为该任务随时分配与修改专属接收人
    notify_openids = None

    # 默认每天晚上汇报时间点 (格式: HH:MM，支持在状态配置中定制)
    default_report_time = "22:00"

    def __init__(self):
        super().__init__()
        # 读取或初始化当前任务的配置参数
        if "report_time" not in self.state:
            self.state["report_time"] = self.default_report_time
        if "last_reported_date" not in self.state:
            self.state["last_reported_date"] = ""
        if "last_metrics" not in self.state:
            self.state["last_metrics"] = {}

    def get_summary(self) -> Dict[str, Any]:
        res = super().get_summary()
        res["task_type"] = "scheduled"
        res["report_time"] = self.state.get("report_time", self.default_report_time)
        return res

    def get_config(self) -> Dict[str, Any]:
        res = super().get_config()
        res["task_type"] = "scheduled"
        res["report_time"] = self.state.get("report_time", self.default_report_time)
        return res

    def update_config(
        self,
        interval_seconds: Optional[int] = None,
        enabled: Optional[bool] = None,
        notify_openids: Optional[Any] = None,
        report_time: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """在线更新课时费日报配置，核心支持设置每天几点推送 (HH:MM)"""
        if report_time is not None:
            cleaned = str(report_time).strip()
            if cleaned:
                self.state["report_time"] = cleaned
                self.save_state()
                logger.info("[%s] 每日运营日报定时推送时间已更新为: %s", self.task_id, cleaned)

        # 内部维持固定 60 秒心跳检查时刻，用户无需也无法在前端修改巡检周期
        return super().update_config(
            interval_seconds=60,
            enabled=enabled,
            notify_openids=notify_openids,
            **kwargs,
        )

    def get_api_config(self) -> tuple[str, str]:
        """获取目标系统的接口地址与通信密钥"""
        api_url = os.getenv(
            "TEACH_FEE_API_URL",
            "http://127.0.0.1:8000/api/v1/internal/daily-metrics",
        ).strip()
        api_key = os.getenv("TEACH_FEE_API_KEY", "watchpatrol_metrics_secret_2026").strip()
        return api_url, api_key

    async def fetch_metrics(self) -> Dict[str, Any]:
        """向目标课时费系统接口发送带密钥的安全请求，拉取最新统计数据"""
        api_url, api_key = self.get_api_config()
        headers = {
            "X-API-KEY": api_key,
            "User-Agent": "WatchPatrol-DailyReporter/1.0",
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

    async def send_report_notification(self, metrics: Dict[str, Any], date_str: str) -> bool:
        """格式化数据并通过微信专属模板推送运营日报卡片"""
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

        # 核心变动摘要
        enroll_status_text = (
            f"新增 {new_users} 人 | AI调用 {ai_usage} 次 | 订阅 {new_subs} 人 (¥{sub_income})"
        )

        # 详细运营指标文本排版
        remark_text = (
            f"📅 统计日期: {date_str}\n"
            f"👥 当日新增: {new_users} 人 (总用户数: {total_users})\n"
            f"🤖 AI使用量: {ai_usage} 次 (反馈 {fb_gen} / 对话 {chat_cnt})\n"
            f"📝 新增课时: {class_records} 条\n"
            f"🔥 活跃人数: {active_users} 人\n"
            f"💎 开通订阅: {new_subs} 人 (充值流水: ¥{sub_income})"
        )

        # 为本次日报推送生成 24 小时专属移动端快照页面
        snap_data = dict(metrics)
        snap_data["date"] = date_str
        snap_id = snapshot_manager.create_snapshot(
            task_id=self.task_id,
            title=f"课时费运营日报 ({date_str})",
            data=snap_data,
            ttl_hours=24,
        )
        click_url = snapshot_manager.get_snapshot_url(snap_id)

        custom_tpl = os.getenv("TEACH_FEE_TEMPLATE_ID") or self.state.get("template_id")

        res = await send_job_alert(
            unit="课时费计算管理平台",
            post="每日核心运营指标日报",
            enroll_status=enroll_status_text,
            remark=remark_text,
            title=f"【每日日报】课时费系统 {date_str} 运营数据汇总",
            to=self.get_notify_openids(),
            click_url=click_url,
            template_id=custom_tpl,
        )

        logger.info(
            "[%s] 每日运营日报已推送: success=%s, msg_id=%s",
            self.task_id,
            res.success,
            res.msg_id,
        )
        return res.success

    async def execute_check(self, force: bool = False) -> Dict[str, Any]:
        """
        执行心跳检查：
        1. 常规轮询 (force=False)：仅在到达指定每晚时间后触发汇报，每日仅汇报一次；未到达或今日已发时完全静默。
        2. 手动强制触发 (force=True)：直接拉取最新运营指标并立即推送到微信。
        """
        now = datetime.now(CHINA_TZ)
        today_str = now.strftime("%Y-%m-%d")
        current_hm = now.strftime("%H:%M")
        target_time = self.state.get("report_time", self.default_report_time)

        # 检查今天是否已经汇报过
        already_reported = (self.state.get("last_reported_date") == today_str)

        # 判定是否满足推送条件：
        # - 手动强制触发 (force=True)：无条件执行推送
        # - 后台定时轮询 (force=False)：必须到达目标时间且今日未推送
        should_send = force or (current_hm >= target_time and not already_reported)

        if not should_send:
            return {
                "status": "idle",
                "current_time": current_hm,
                "target_time": target_time,
                "already_reported_today": already_reported,
                "message": f"当前时间 {current_hm}，目标汇报时刻 {target_time}（{'今日已推送' if already_reported else '等待时间到达'}）",
            }

        logger.info(
            "[%s] 触发每日运营数据汇报 (force=%s, 当前时间=%s, 目标时刻=%s)...",
            self.task_id, force, current_hm, target_time
        )
        metrics = await self.fetch_metrics()

        # 触发微信推送
        notified = await self.send_report_notification(metrics, today_str)

        # 仅在自然到达定时时刻汇报时标记今日已发；手动测试不阻止当晚定时推送
        if not force:
            self.state["last_reported_date"] = today_str

        self.state["last_metrics"] = metrics
        self.state["last_check_time"] = now.strftime("%Y-%m-%d %H:%M:%S")

        # 记录历史流水
        self.record_history({
            "date": today_str,
            "metrics": metrics,
            "notified": notified,
            "report_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_type": "manual" if force else "scheduled",
        })
        self.save_state()

        return {
            "status": "reported",
            "trigger_type": "manual" if force else "scheduled",
            "date": today_str,
            "metrics": metrics,
            "notified": notified,
        }

