"""
微信通知联系人通讯录管理模块
负责维护联系人姓名、微信 OpenID 及备注信息的持久化存储与管理。
支持联系人增删改查、单人连通性测试及与任务接收人的动态关联。
"""

import os
import json
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger("core.contact")


class ContactManager:
    """联系人通讯录管理器（单例）"""

    def __init__(self, data_dir: Optional[Path] = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.data_dir = data_dir or (base_dir / "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "contacts.json"
        self._contacts: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        """载入通讯录，若为空则自动将系统环境变量中的管理员收录为首个默认联系人"""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self._contacts = json.load(f)
                    return
            except Exception as e:
                logger.error("读取联系人文件失败: %s", e)

        # 首次初始化：自动导入环境变量里的 WECHAT_OPENID
        self._contacts = []
        default_oid = (os.getenv("WECHAT_OPENID") or os.getenv("ADMIN_OPENID", "")).strip()
        if default_oid:
            # 兼容逗号分隔的多个 openid
            for idx, oid in enumerate(default_oid.split(",")):
                oid_clean = oid.strip()
                if oid_clean:
                    self._contacts.append({
                        "id": f"contact_default_{idx + 1}",
                        "name": f"系统管理员{f' {idx+1}' if idx > 0 else ''}",
                        "openid": oid_clean,
                        "remark": "来自系统环境变量默认配置",
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
            self._save()

    def _save(self):
        """原子化保存通讯录到 JSON 文件"""
        temp_file = self.file_path.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._contacts, f, ensure_ascii=False, indent=2)
            temp_file.replace(self.file_path)
        except Exception as e:
            logger.error("保存联系人文件失败: %s", e)

    def list_contacts(self) -> List[Dict[str, Any]]:
        """获取所有联系人列表"""
        return list(self._contacts)

    def get_contact(self, contact_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 查询单个联系人"""
        for c in self._contacts:
            if c["id"] == contact_id:
                return c
        return None

    def find_by_openid(self, openid: str) -> Optional[Dict[str, Any]]:
        """按 OpenID 查找联系人"""
        target = openid.strip()
        for c in self._contacts:
            if c.get("openid", "").strip() == target:
                return c
        return None

    def add_contact(self, name: str, openid: str, remark: str = "") -> Dict[str, Any]:
        """新增联系人"""
        name_clean = str(name).strip()
        openid_clean = str(openid).strip()
        remark_clean = str(remark).strip()

        if not name_clean:
            raise ValueError("联系人姓名/备注名不能为空")
        if not openid_clean:
            raise ValueError("微信 OpenID 不能为空")

        # 检查 openid 是否已存在
        exist = self.find_by_openid(openid_clean)
        if exist:
            raise ValueError(f"该 OpenID 已存在于联系人【{exist['name']}】中，不可重复添加")

        contact_id = f"c_{uuid.uuid4().hex[:8]}"
        new_item = {
            "id": contact_id,
            "name": name_clean,
            "openid": openid_clean,
            "remark": remark_clean,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._contacts.append(new_item)
        self._save()
        logger.info("成功添加新联系人: [%s] %s (%s)", contact_id, name_clean, openid_clean)
        return new_item

    def update_contact(
        self,
        contact_id: str,
        name: Optional[str] = None,
        openid: Optional[str] = None,
        remark: Optional[str] = None,
    ) -> Dict[str, Any]:
        """更新联系人信息"""
        contact = self.get_contact(contact_id)
        if not contact:
            raise KeyError(f"联系人不存在: {contact_id}")

        if name is not None:
            name_clean = str(name).strip()
            if not name_clean:
                raise ValueError("姓名不能为空")
            contact["name"] = name_clean

        if openid is not None:
            openid_clean = str(openid).strip()
            if not openid_clean:
                raise ValueError("OpenID 不能为空")
            # 校验唯一性
            exist = self.find_by_openid(openid_clean)
            if exist and exist["id"] != contact_id:
                raise ValueError(f"该 OpenID 已被联系人【{exist['name']}】使用")
            contact["openid"] = openid_clean

        if remark is not None:
            contact["remark"] = str(remark).strip()

        contact["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save()
        return contact

    def delete_contact(self, contact_id: str) -> bool:
        """删除联系人"""
        before_len = len(self._contacts)
        self._contacts = [c for c in self._contacts if c["id"] != contact_id]
        if len(self._contacts) < before_len:
            self._save()
            logger.info("已删除联系人: %s", contact_id)
            return True
        return False

    def resolve_openids(self, identifiers: List[str]) -> List[str]:
        """
        根据传入的标识符列表（可能是联系人 ID，也可能是直接的原始 openid）
        统一解析为实际有效的微信 openid 列表
        """
        resolved = []
        id_map = {c["id"]: c["openid"] for c in self._contacts}
        for item in identifiers:
            item_clean = str(item).strip()
            if not item_clean:
                continue
            if item_clean in id_map:
                resolved.append(id_map[item_clean])
            else:
                # 兼容直接传入的 openid
                resolved.append(item_clean)
        # 去重保持顺序
        return list(dict.fromkeys(resolved))

    def get_contact_names(self, identifiers: List[str]) -> List[str]:
        """根据标识符解析展示用的联系人名称"""
        names = []
        id_map = {c["id"]: c["name"] for c in self._contacts}
        openid_map = {c["openid"]: c["name"] for c in self._contacts}

        for item in identifiers:
            item_clean = str(item).strip()
            if not item_clean:
                continue
            if item_clean in id_map:
                names.append(id_map[item_clean])
            elif item_clean in openid_map:
                names.append(openid_map[item_clean])
            else:
                # 未知则脱敏展示
                if len(item_clean) > 8:
                    names.append(f"{item_clean[:4]}***{item_clean[-4:]}")
                else:
                    names.append(item_clean)
        return names


# 全局联系人单例
contact_manager = ContactManager()
