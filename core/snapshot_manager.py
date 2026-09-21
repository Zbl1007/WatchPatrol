"""
时效快照数据管理模块
为微信模板消息推送生成独立的只读专属移动端 HTML 页面，
默认设置 24 小时（1天）有效期，过期后自动物理销毁清理。
"""

import os
import json
import secrets
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger("core.snapshot")

BASE_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = BASE_DIR / "data" / "snapshots"


class SnapshotManager:
    """移动端时效快照生命周期管理器"""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or SNAPSHOT_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def create_snapshot(
        self,
        task_id: str,
        title: str,
        data: Dict[str, Any],
        ttl_hours: int = 24,
    ) -> str:
        """
        创建一个具有时效性（默认 24 小时）的数据快照

        :param task_id: 任务标识 (如 jd_offer_3jobs, teach_fee_daily_report)
        :param title: 快照标题
        :param data: 结构化业务数据体
        :param ttl_hours: 有效期小时数（默认 24 小时）
        :return: 快照短唯一 ID (例如 8 位安全短串)
        """
        # 生成 8 位短安全 URL 标识
        snapshot_id = secrets.token_urlsafe(6).replace("-", "").replace("_", "")
        if len(snapshot_id) < 8:
            snapshot_id = secrets.token_hex(4)

        now = datetime.now()
        expire_time = now + timedelta(hours=ttl_hours)

        snapshot_record = {
            "snapshot_id": snapshot_id,
            "task_id": task_id,
            "title": title,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "expire_at": expire_time.strftime("%Y-%m-%d %H:%M:%S"),
            "ttl_hours": ttl_hours,
            "data": data,
        }

        filepath = self.storage_dir / f"{snapshot_id}.json"
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(snapshot_record, f, ensure_ascii=False, indent=2)
            logger.info("[%s] 已成功生成时效快照: id=%s, 过期时间=%s", task_id, snapshot_id, snapshot_record["expire_at"])
            return snapshot_id
        except Exception as e:
            logger.error("写入快照文件失败: %s", e)
            raise

    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """
        读取快照数据。若已过期则自动物理删除文件并返回过期标志。
        """
        filepath = self.storage_dir / f"{snapshot_id}.json"
        if not filepath.exists():
            return None

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                record = json.load(f)
        except Exception as e:
            logger.error("读取快照文件异常 [%s]: %s", snapshot_id, e)
            return None

        # 检查是否过期
        expire_at_str = record.get("expire_at", "")
        if expire_at_str:
            try:
                expire_dt = datetime.strptime(expire_at_str, "%Y-%m-%d %H:%M:%S")
                if datetime.now() > expire_dt:
                    # 已过期，立即物理销毁文件
                    logger.info("快照 [%s] 已过有效期 (%s)，立即物理删除销毁", snapshot_id, expire_at_str)
                    try:
                        filepath.unlink(missing_ok=True)
                    except Exception as err:
                        logger.warning("删除过期快照文件失败: %s", err)
                    return {"expired": True, "expire_at": expire_at_str}
            except ValueError:
                pass

        return record

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """手动删除指定快照文件"""
        filepath = self.storage_dir / f"{snapshot_id}.json"
        if filepath.exists():
            filepath.unlink(missing_ok=True)
            return True
        return False

    def cleanup_all_expired(self) -> int:
        """清理所有已过期的快照文件，保证磁盘零冗余"""
        count = 0
        now = datetime.now()
        for f in self.storage_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    record = json.load(fp)
                expire_at_str = record.get("expire_at", "")
                if expire_at_str:
                    expire_dt = datetime.strptime(expire_at_str, "%Y-%m-%d %H:%M:%S")
                    if now > expire_dt:
                        f.unlink(missing_ok=True)
                        count += 1
            except Exception:
                continue
        if count > 0:
            logger.info("自动清理已过期快照文件 %d 个", count)
        return count

    def get_snapshot_url(self, snapshot_id: str) -> str:
        """
        生成快照完整的外部访问短链接
        支持通过环境变量 PUBLIC_BASE_URL 指定公网域名/服务器地址
        """
        base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            # 若未配置，优先回退到课时费已配置的域名或本地默认
            base_url = "http://127.0.0.1:8080"
        return f"{base_url}/s/{snapshot_id}"


# 导出全局单例
snapshot_manager = SnapshotManager()
