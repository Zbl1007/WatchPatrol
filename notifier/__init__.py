"""
通知推送模块
统一导出微信推送服务与快捷方法
"""

from typing import Optional, Dict, Any
from .wechat import WeChatNotifier, WeChatResult, format_enroll_status

# 全局默认单例
wechat_notifier = WeChatNotifier()


async def send_wechat_notice(
    title: str,
    content: str = "",
    to: Optional[str] = None,
    event_type: str = "岗位监控提醒",
    remark: str = "",
    click_url: Optional[str] = None,
    template_data: Optional[Dict[str, Any]] = None,
) -> WeChatResult:
    """
    异步发送微信通知（使用全局默认配置）
    """
    return await wechat_notifier.send(
        title=title,
        content=content,
        to=to,
        event_type=event_type,
        remark=remark,
        click_url=click_url,
        template_data=template_data,
    )


def send_wechat_sync(
    title: str,
    content: str = "",
    to: Optional[str] = None,
    event_type: str = "岗位监控提醒",
    remark: str = "",
    click_url: Optional[str] = None,
    template_data: Optional[Dict[str, Any]] = None,
) -> WeChatResult:
    """
    同步发送微信通知（使用全局默认配置）
    """
    return wechat_notifier.send_sync(
        title=title,
        content=content,
        to=to,
        event_type=event_type,
        remark=remark,
        click_url=click_url,
        template_data=template_data,
    )


async def send_job_alert(
    unit: str,
    post: str,
    enroll_status: str,
    change_time: Optional[str] = None,
    remark: str = "点击卡片可查看官方岗位详情",
    title: str = "【招考变动】岗位报名人数发生变化！",
    to: Optional[str] = None,
    click_url: Optional[str] = None,
) -> WeChatResult:
    """
    异步发送专属岗位变动提醒消息
    """
    return await wechat_notifier.send_job_alert(
        unit=unit,
        post=post,
        enroll_status=enroll_status,
        change_time=change_time,
        remark=remark,
        title=title,
        to=to,
        click_url=click_url,
    )


def send_job_alert_sync(
    unit: str,
    post: str,
    enroll_status: str,
    change_time: Optional[str] = None,
    remark: str = "点击卡片可查看官方岗位详情",
    title: str = "【招考变动】岗位报名人数发生变化！",
    to: Optional[str] = None,
    click_url: Optional[str] = None,
) -> WeChatResult:
    """
    同步发送专属岗位变动提醒消息
    """
    return wechat_notifier.send_job_alert_sync(
        unit=unit,
        post=post,
        enroll_status=enroll_status,
        change_time=change_time,
        remark=remark,
        title=title,
        to=to,
        click_url=click_url,
    )


__all__ = [
    "WeChatNotifier",
    "WeChatResult",
    "wechat_notifier",
    "send_wechat_notice",
    "send_wechat_sync",
    "send_job_alert",
    "send_job_alert_sync",
    "format_enroll_status",
]
