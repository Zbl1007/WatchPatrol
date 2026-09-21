"""
重点招录岗位报名监控插件
继承自 BasePlugin，负责监控指定招考岗位的实时报名人数变动并自动推送微信模板消息。
"""

import os
import asyncio
import logging
from typing import Dict, List, Any
from datetime import datetime
import httpx

from core.plugin_base import BasePlugin
from core.snapshot_manager import snapshot_manager
from notifier import send_job_alert, format_enroll_status

logger = logging.getLogger("plugins.jd_offer")

API_BASE_URL = "http://211.166.6.109:9998/enroll/post/listVisitor"

DEFAULT_TARGET_JOBS = [
    {
        "id": "00cde71d64bd53fce6e4afb8e06baf51",
        "post_code": "QDG20260079",
        "name": "助理工程师",
        "expected_unit": "重点招考单位",
    },
    {
        "id": "41be2cd816763543765bc16ccb285e28",
        "post_code": "QDG20260080",
        "name": "助理工程师",
        "expected_unit": "重点招考单位",
    },
    {
        "id": "0b12157fc007670ba04343ce11e7c149",
        "post_code": "QDG20260081",
        "name": "助理工程师",
        "expected_unit": "重点招考单位",
    },
]


class JdOfferPlugin(BasePlugin):
    """重点岗位招录监控插件"""

    plugin_id = "jd_offer"
    plugin_name = "重点岗位招录监控"
    description = "定时抓取官方招考接口，比对目标岗位报考人数增减，出现变动即刻推送微信卡片与时效快照。"
    author = "系统内置"
    version = "2.0.0"
    supported_types = ["interval"]
    default_type = "interval"
    default_interval = 60

    async def fetch_single_job(
        self, client: httpx.AsyncClient, target: Dict[str, str]
    ) -> Any:
        job_id = target["id"]
        post_code = target["post_code"]
        url = f"{API_BASE_URL}?queryStr={post_code}"

        try:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            res_json = resp.json()

            items = res_json.get("data", {}).get("data", [])
            for item in items:
                if item.get("id") == job_id:
                    unit_clean = (
                        item.get("needHandsUnit")
                        or item.get("unit")
                        or target.get("expected_unit", "")
                    ).replace("\n", " ").strip()
                    station_clean = (item.get("forceStation") or "").replace("\n", " ").strip()

                    return {
                        "id": job_id,
                        "post_code": item.get("postCode", post_code),
                        "unit": unit_clean,
                        "post": item.get("post", target.get("name", "助理工程师")),
                        "quantity": int(item.get("quantity", 1)),
                        "post_num": int(item.get("postNum", 0)),
                        "subject": item.get("subject", ""),
                        "education": item.get("educationBackground", ""),
                        "station": station_clean,
                        "tel": (item.get("telPhone") or "").replace("\n", " / ").strip(),
                        "do_job": item.get("doJob", ""),
                        "query_url": url,
                        "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }

            return None
        except Exception as e:
            logger.error("[%s] 抓取岗位 [%s] 异常: %s", self.plugin_id, post_code, e)
            return None

    async def run(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        task_id = context.get("task_id", "jd_offer")
        state = context.get("state", {})
        notify_openids = context.get("notify_openids", [])
        save_state = context.get("save_state")
        record_history = context.get("record_history")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        targets = config.get("targets") or DEFAULT_TARGET_JOBS

        async with httpx.AsyncClient() as client:
            tasks = [self.fetch_single_job(client, t) for t in targets]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        changes = []
        jobs_dict = state.setdefault("jobs", {})

        for target, res in zip(targets, results):
            if isinstance(res, Exception) or not res:
                continue

            job_id = res["id"]
            new_num = res["post_num"]
            quantity = res["quantity"]
            post_code = res["post_code"]
            unit = res["unit"]
            post_name = res["post"]

            old_job = jobs_dict.get(job_id)
            if not old_job:
                jobs_dict[job_id] = res
            else:
                old_num = old_job.get("post_num", 0)
                jobs_dict[job_id] = res

                if new_num != old_num:
                    change_text = format_enroll_status(old_num, new_num, quantity)
                    logger.warning(
                        "[%s 告警] %s (%s) 报名人数变动: %s",
                        task_id, post_name, post_code, change_text
                    )

                    # 生成 24 小时专属快照
                    snap_id = snapshot_manager.create_snapshot(
                        task_id=task_id,
                        title=f"{post_name} ({post_code}) 岗位变动快照",
                        data=res,
                        ttl_hours=24,
                    )
                    snap_url = snapshot_manager.get_snapshot_url(snap_id)

                    # 仅在配置了通知目标时推送
                    push_res = None
                    if notify_openids:
                        push_res = await send_job_alert(
                            unit=unit,
                            post=f"{post_name} (代码: {post_code})",
                            enroll_status=change_text,
                            change_time=now_str,
                            remark="点击卡片即可在微信中直接查看美观的岗位详情\n本快照具备 24 小时时效性",
                            click_url=snap_url,
                            to=notify_openids,
                            template_id=os.getenv("WECHAT_TEMPLATE_ID"),
                        )

                    change_record = {
                        "time": now_str,
                        "job_id": job_id,
                        "post_code": post_code,
                        "post_name": post_name,
                        "unit": unit,
                        "old_num": old_num,
                        "new_num": new_num,
                        "change_text": change_text,
                        "notified": push_res.success if push_res else False,
                        "msg_id": push_res.msg_id if push_res else "",
                    }
                    changes.append(change_record)
                    if callable(record_history):
                        record_history(change_record)

        state["jobs"] = jobs_dict
        if callable(save_state):
            save_state()

        return {
            "monitored_count": len(jobs_dict),
            "changes_count": len(changes),
            "changes": changes,
        }
