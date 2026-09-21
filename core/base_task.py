"""
通用监控任务基类模块
为所有自定义监控脚本提供统一的生命周期、状态持久化与执行接口。
"""

import os
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("core.task")


class BaseTask(ABC):
    """
    监控任务基类
    所有自定义监控脚本需继承此类并实现 execute_check() 方法。
    """

    task_id: str = "base_task"
    task_name: str = "基础监控任务"
    interval_seconds: int = 60
    enabled: bool = True
    # 指定本任务绑定的微信接收人 openid (支持 str 或 List[str]，留空则使用全局默认)
    notify_openids: Optional[Any] = None

    def __init__(self, data_dir: Optional[Path] = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.data_dir = data_dir or (base_dir / "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / f"{self.task_id}_state.json"

        # 运行时状态指标
        self.last_run_time: Optional[str] = None
        self.last_status: str = "ready"  # ready, running, success, error
        self.last_error: Optional[str] = None
        self.run_count: int = 0

        # 初始化载入持久化数据
        self.state: Dict[str, Any] = self.load_state()

    def load_state(self) -> Dict[str, Any]:
        """从对应的数据文本文件载入状态，并恢复已保存的用户在线配置"""
        data = None
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error("[%s] 读取状态文件失败: %s", self.task_id, e)

        if data is None:
            data = {
                "task_id": self.task_id,
                "last_check_time": "",
                "history": [],
            }
            self.save_state(data)

        # 恢复持久化过的用户在线配置
        saved_config = data.get("config", {})
        if "interval_seconds" in saved_config:
            self.interval_seconds = int(saved_config["interval_seconds"])
        if "enabled" in saved_config:
            self.enabled = bool(saved_config["enabled"])
        if "notify_openids" in saved_config:
            self.notify_openids = saved_config["notify_openids"]

        return data

    def save_state(self, state_data: Optional[Dict[str, Any]] = None):
        """原子化保存状态至 JSON 文本"""
        if state_data is not None:
            self.state = state_data

        temp_file = self.state_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
            temp_file.replace(self.state_file)
        except Exception as e:
            logger.error("[%s] 保存状态文件失败: %s", self.task_id, e)

    def record_history(self, record: Dict[str, Any], max_items: int = 50):
        """向历史流水追加一条记录（自动截取最新 max_items 条）"""
        if "time" not in record:
            record["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        history = self.state.setdefault("history", [])
        history.insert(0, record)
        if len(history) > max_items:
            self.state["history"] = history[:max_items]
        self.save_state()

    @abstractmethod
    async def execute_check(self) -> Dict[str, Any]:
        """
        业务检测核心方法（子类必须实现）
        在该方法内实现目标拉取、数据比对、微信推送与流水记录。

        Returns:
            Dict[str, Any]: 本次检查的摘要信息（如变动条数、变动详情等）
        """
        pass

    async def run_once(self) -> Dict[str, Any]:
        """
        执行单次检测（带统一的生命周期追踪与异常防护）
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_run_time = now_str
        self.last_status = "running"
        self.last_error = None
        self.run_count += 1

        logger.info("[%s - %s] 开始执行检查第 %d 次", self.task_id, self.task_name, self.run_count)
        start_ts = datetime.now()

        try:
            result = await self.execute_check()
            elapsed_ms = int((datetime.now() - start_ts).total_seconds() * 1000)

            self.last_status = "success"
            self.state["last_check_time"] = now_str
            self.save_state()

            logger.info("[%s - %s] 检查成功完成，耗时 %d ms", self.task_id, self.task_name, elapsed_ms)
            return {
                "success": True,
                "task_id": self.task_id,
                "time": now_str,
                "elapsed_ms": elapsed_ms,
                "data": result,
            }
        except Exception as e:
            elapsed_ms = int((datetime.now() - start_ts).total_seconds() * 1000)
            self.last_status = "error"
            self.last_error = str(e)
            logger.error("[%s - %s] 检查执行异常: %s (耗时 %d ms)", self.task_id, self.task_name, e, elapsed_ms)
            return {
                "success": False,
                "task_id": self.task_id,
                "time": now_str,
                "elapsed_ms": elapsed_ms,
                "error": str(e),
            }

    def get_notify_openids(self) -> list:
        """获取该任务绑定的微信接收人 openid 列表"""
        targets = []
        if self.notify_openids:
            if isinstance(self.notify_openids, (list, tuple, set)):
                targets = [str(x).strip() for x in self.notify_openids if str(x).strip()]
            elif isinstance(self.notify_openids, str) and self.notify_openids.strip():
                targets = [x.strip() for x in self.notify_openids.split(",") if x.strip()]

        # 若未指定，回退读取环境变量的全局管理员
        if not targets:
            default_oid = os.getenv("WECHAT_OPENID") or os.getenv("ADMIN_OPENID", "")
            if default_oid:
                targets = [x.strip() for x in default_oid.split(",") if x.strip()]

        return targets

    def get_masked_notify_targets(self) -> list:
        """获取脱敏后的微信接收人标识用于展示，例如 ovAe***jc"""
        raw_list = self.get_notify_openids()
        masked = []
        for oid in raw_list:
            if len(oid) > 8:
                masked.append(f"{oid[:4]}***{oid[-4:]}")
            else:
                masked.append(oid)
        return masked

    def get_summary(self) -> Dict[str, Any]:
        """获取任务当前的配置与运行状态摘要"""
        masked_targets = self.get_masked_notify_targets()
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "notify_targets": masked_targets,
            "notify_count": len(masked_targets),
            "is_custom_notify": bool(self.notify_openids),
            "last_run_time": self.last_run_time or self.state.get("last_check_time", ""),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "history_count": len(self.state.get("history", [])),
        }

    def get_config(self) -> Dict[str, Any]:
        """获取任务当前的可在线配置详情"""
        raw_openids = self.notify_openids or ""
        if isinstance(raw_openids, list):
            raw_openids_text = ", ".join(raw_openids)
        else:
            raw_openids_text = str(raw_openids)

        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "notify_openids": self.get_notify_openids(),
            "raw_openids_text": raw_openids_text,
            "is_custom_notify": bool(self.notify_openids),
        }

    def update_config(
        self,
        interval_seconds: Optional[int] = None,
        enabled: Optional[bool] = None,
        notify_openids: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """在线更新任务配置并即时写回文本持久化"""
        if interval_seconds is not None:
            if interval_seconds < 5:
                raise ValueError("巡检周期不得低于 5 秒")
            self.interval_seconds = int(interval_seconds)

        if enabled is not None:
            self.enabled = bool(enabled)

        if notify_openids is not None:
            if isinstance(notify_openids, str):
                cleaned = [x.strip() for x in notify_openids.split(",") if x.strip()]
                self.notify_openids = cleaned if cleaned else None
            elif isinstance(notify_openids, (list, tuple, set)):
                cleaned = [str(x).strip() for x in notify_openids if str(x).strip()]
                self.notify_openids = cleaned if cleaned else None
            else:
                self.notify_openids = None

        # 写入持久化文本
        self.state["config"] = {
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "notify_openids": self.notify_openids,
        }
        self.save_state()
        logger.info(
            "[%s] 任务配置已在线更新: interval=%ds, enabled=%s, 接收人数=%d",
            self.task_id,
            self.interval_seconds,
            self.enabled,
            len(self.get_notify_openids()),
        )
        return self.get_config()
