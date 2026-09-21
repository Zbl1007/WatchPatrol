"""
招录重点岗位报名监控任务插件
继承自 BaseTask，负责目标招考岗位的实时报名人数监控与微信模板消息推送。
"""

import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
import httpx

from core.base_task import BaseTask
from notifier import send_job_alert, format_enroll_status

logger = logging.getLogger("tasks.jd_offer")

API_BASE_URL = "http://211.166.6.109:9998/enroll/post/listVisitor"

TARGET_JOBS = [
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


class JdOfferMonitorTask(BaseTask):
    """重点招考岗位监控任务"""

    task_id = "jd_offer_3jobs"
    task_name = "重点岗位招录监控"
    interval_seconds = 60
    enabled = True

    # 专属通知目标：若设为 None，系统将自动读取 .env 中的全局 WECHAT_OPENID
    # 也可在 Web 控制台点击「配置」随时为该任务动态分配与修改接收人
    notify_openids = None

    def __init__(self, targets: Optional[List[Dict[str, str]]] = None):
        super().__init__()
        # 保持与已有 data/jobs_state.json 文件的向后完全兼容
        self.state_file = self.data_dir / "jobs_state.json"
        self.targets = targets or TARGET_JOBS
        self.state = self.load_state()

    def load_state(self) -> Dict[str, Any]:
        """扩展基类状态结构，包含 jobs 字典"""
        state = super().load_state()
        state.setdefault("jobs", {})
        return state

    async def fetch_single_job(
        self, client: httpx.AsyncClient, target: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """向官方接口精确查询指定岗位代码数据"""
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

            logger.warning("[%s] 接口未返回目标岗位: %s (%s)", self.task_id, job_id, post_code)
            return None
        except Exception as e:
            logger.error("[%s] 请求官方接口异常 [%s]: %s", self.task_id, post_code, e)
            return None

    async def execute_check(self) -> Dict[str, Any]:
        """执行全量岗位抓取、人数比对与变动推送"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        async with httpx.AsyncClient() as client:
            tasks = [self.fetch_single_job(client, t) for t in self.targets]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        changes = []
        jobs_dict = self.state.setdefault("jobs", {})

        for target, res in zip(self.targets, results):
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
                # 首次收录基准值
                logger.info("[%s] 首次收录基准岗位: %s (%s) 报名人数: %d", self.task_id, post_name, post_code, new_num)
                jobs_dict[job_id] = res
            else:
                old_num = old_job.get("post_num", 0)
                jobs_dict[job_id] = res

                if new_num != old_num:
                    change_text = format_enroll_status(old_num, new_num, quantity)
                    logger.warning(
                        "[%s 告警] %s (%s) 报名人数变动: %s",
                        self.task_id,
                        post_name,
                        post_code,
                        change_text,
                    )

                    # 触发微信专属模板推送（仅推给本任务绑定的接收人）
                    push_res = await send_job_alert(
                        unit=unit,
                        post=f"{post_name} (代码: {post_code})",
                        enroll_status=change_text,
                        change_time=now_str,
                        remark="点击卡片可查看官方岗位详情",
                        click_url=res.get("query_url"),
                        to=self.get_notify_openids(),
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
                        "notified": push_res.success,
                        "notified_targets": self.get_masked_notify_targets(),
                        "msg_id": push_res.msg_id,
                    }
                    changes.append(change_record)
                    self.record_history(change_record)
                else:
                    logger.info("[%s] %s (%s) 报名人数稳定: %d 人", self.task_id, post_name, post_code, new_num)

        self.state["jobs"] = jobs_dict
        self.save_state()

        return {
            "monitored_count": len(jobs_dict),
            "changes_count": len(changes),
            "changes": changes,
        }
