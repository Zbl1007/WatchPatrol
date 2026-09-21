"""
示例通用网页/服务心跳探活插件模板
【供专职开发人员参考与扩展的标准示例】

开发说明:
1. 继承 BasePlugin；
2. 填写 plugin_id, plugin_name, description, author 等基础元数据；
3. 在 run() 方法内编写你的核心抓取或检测逻辑；
4. 完成后只需将文件丢入 plugins/ 目录，Web 界面即可自动识别并呈现在添加任务下拉列表中！
"""

import logging
from typing import Dict, Any
from datetime import datetime
import httpx

from core.plugin_base import BasePlugin
from notifier import send_job_alert

logger = logging.getLogger("plugins.heartbeat")


class ServiceHeartbeatPlugin(BasePlugin):
    """服务/网页可用性探活插件模板"""

    # 1. 唯一标识 (全英文小写)
    plugin_id = "service_heartbeat"

    # 2. 中文名称 (在 Web 界面下拉列表中展示)
    plugin_name = "通用服务可用性探活"

    # 3. 插件功能说明
    description = "定时对指定的目标 HTTP/HTTPS 服务发送探活请求，若服务异常或响应超时，自动向指定联系人发送微信预警。"

    # 4. 维护人员信息
    author = "运维扩展组"
    version = "1.0.0"

    # 5. 支持的调度方式: 'interval' (周期轮询), 'scheduled' (每日定时)
    supported_types = ["interval"]
    default_type = "interval"
    default_interval = 120  # 默认 2 分钟一次

    # 6. 该脚本需要的自定义配置字段（前端可按此结构动态配置）
    config_fields = [
        {
            "key": "target_url",
            "label": "探活目标 URL",
            "type": "text",
            "default": "https://dam.520315.xyz",
            "placeholder": "https://example.com/health",
        },
        {
            "key": "timeout_seconds",
            "label": "超时时间 (秒)",
            "type": "number",
            "default": 10,
        },
    ]

    async def run(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """
        核心检测入口:
        - context: 调度引擎传入的上下文环境
        - config: 用户在前端为该任务定制的参数
        """
        task_id = context.get("task_id")
        task_name = context.get("task_name")
        notify_openids = context.get("notify_openids", [])
        record_history = context.get("record_history")

        target_url = config.get("target_url", "https://dam.520315.xyz").strip()
        timeout = float(config.get("timeout_seconds", 10))
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        logger.info("[%s] 开始对目标地址执行探活: %s (超时 %ss)", task_id, target_url, timeout)

        status_code = None
        is_healthy = False
        error_msg = None
        elapsed_ms = 0

        try:
            async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
                start_t = datetime.now()
                resp = await client.get(target_url)
                elapsed_ms = int((datetime.now() - start_t).total_seconds() * 1000)
                status_code = resp.status_code
                is_healthy = (200 <= status_code < 400)
        except Exception as e:
            error_msg = str(e)
            is_healthy = False

        # 如果服务出现异常且配置了通知对象，发送微信预警
        notified = False
        if not is_healthy and notify_openids:
            fail_reason = f"HTTP {status_code}" if status_code else f"连接错误: {error_msg}"
            push_res = await send_job_alert(
                unit="系统健康探活预警",
                post=task_name,
                enroll_status=f"服务不可用 ({fail_reason})",
                change_time=now_str,
                remark=f"目标: {target_url}\n响应耗时: {elapsed_ms}ms\n请尽快登录服务器排查故障！",
                to=notify_openids,
            )
            notified = push_res.success

        # 记录本次探活流水
        record = {
            "time": now_str,
            "target_url": target_url,
            "is_healthy": is_healthy,
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "error": error_msg,
            "notified": notified,
        }
        if callable(record_history):
            record_history(record)

        return {
            "status": "healthy" if is_healthy else "unhealthy",
            "target_url": target_url,
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "error": error_msg,
            "notified": notified,
        }
