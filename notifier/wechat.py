"""
微信公众号（测试号）模板消息推送模块
提供开箱即用的微信推送封装，支持异步协程与同步函数调用。
"""

import os
import asyncio
import logging
import concurrent.futures
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from dotenv import load_dotenv

# 优先载入当前目录或上层目录的 .env 文件
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

logger = logging.getLogger("notifier.wechat")


@dataclass
class WeChatResult:
    """微信消息发送结果"""
    success: bool
    msg_id: Optional[str] = None
    err_code: Optional[int] = None
    err_msg: Optional[str] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.success:
            return f"<WeChatResult success=True msg_id={self.msg_id}>"
        return f"<WeChatResult success=False err_code={self.err_code} err_msg={self.err_msg}>"


def format_enroll_status(old_num: int, new_num: int, quota: Optional[int] = None) -> str:
    """
    格式化报名人数变动文案（使用标准的 '->' 箭头符号，清晰直观且绝不乱码）
    例如：1人 -> 2人 (增加 1 人 / 招录 1 人)
    """
    diff = new_num - old_num
    if diff > 0:
        diff_text = f"增加 {diff} 人"
    elif diff < 0:
        diff_text = f"减少 {abs(diff)} 人"
    else:
        diff_text = "人数持平"

    res = f"{old_num}人 -> {new_num}人 ({diff_text}"
    if quota is not None:
        res += f" / 招录 {quota} 人)"
    else:
        res += ")"
    return res


class WeChatNotifier:
    """微信公众号/测试号模板消息推送器"""

    TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
    SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/template/send"

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        template_id: Optional[str] = None,
        default_openid: Optional[str] = None,
        default_url: str = "",
        timeout: float = 10.0,
    ):
        """
        初始化微信推送器。参数不传时自动从环境变量读取。

        Args:
            app_id: 微信测试号或公众号 appID (环境变量: WECHAT_APP_ID)
            app_secret: 微信测试号或公众号 appsecret (环境变量: WECHAT_APP_SECRET)
            template_id: 消息模板 ID (环境变量: WECHAT_TEMPLATE_ID)
            default_openid: 默认接收消息的用户 openid (环境变量: WECHAT_OPENID / ADMIN_OPENID)
            default_url: 用户点击模板消息后默认跳转链接
            timeout: HTTP 请求超时秒数
        """
        self.app_id = (
            app_id
            or os.getenv("WECHAT_APP_ID")
            or os.getenv("WECHAT_OA_APP_ID", "")
        ).strip()
        self.app_secret = (
            app_secret
            or os.getenv("WECHAT_APP_SECRET")
            or os.getenv("WECHAT_OA_APP_SECRET", "")
        ).strip()
        self.template_id = (
            template_id
            or os.getenv("WECHAT_TEMPLATE_ID")
            or os.getenv("WECHAT_OA_TEMPLATE_ID", "")
        ).strip()
        self.default_openid = (
            default_openid
            or os.getenv("WECHAT_OPENID")
            or os.getenv("ADMIN_OPENID", "")
        ).strip()
        self.default_url = default_url
        self.timeout = timeout

        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._token_lock = asyncio.Lock()

    def is_configured(self) -> bool:
        """检查核心凭证是否均已配置"""
        return bool(self.app_id and self.app_secret and self.template_id)

    async def get_access_token(self, force: bool = False) -> str:
        """
        获取接口调用凭据 access_token（带内存缓存与提前刷新机制）

        Args:
            force: 是否强制重新向微信服务器获取
        """
        if not self.app_id or not self.app_secret:
            raise ValueError("未配置 WECHAT_APP_ID 或 WECHAT_APP_SECRET")

        async with self._token_lock:
            # 存在有效缓存且未过期（预留 5 分钟安全缓冲）
            if not force and self._access_token and self._token_expires_at:
                if datetime.now() < self._token_expires_at - timedelta(minutes=5):
                    return self._access_token

            params = {
                "grant_type": "client_credential",
                "appid": self.app_id,
                "secret": self.app_secret,
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(self.TOKEN_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            if "access_token" not in data:
                err_code = data.get("errcode")
                err_msg = data.get("errmsg", "未知错误")
                raise RuntimeError(f"获取微信 access_token 失败 [{err_code}]: {err_msg}")

            self._access_token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            logger.info("微信 access_token 刷新成功，有效期 %d 秒", expires_in)
            return self._access_token

    async def send(
        self,
        title: str,
        content: str = "",
        to: Optional[Any] = None,
        event_type: str = "系统通知",
        remark: str = "",
        click_url: Optional[str] = None,
        template_data: Optional[Dict[str, Any]] = None,
        template_id: Optional[str] = None,
    ) -> WeChatResult:
        """
        异步发送微信模板消息。支持单个 openid 或多个 openid 列表。
        """
        active_template_id = template_id or self.template_id
        if not self.is_configured() and not active_template_id:
            return WeChatResult(
                success=False,
                err_code=-2,
                err_msg="微信凭证未配置完整，请设置 app_id, app_secret, template_id",
            )

        # 解析指定接收人列表
        target_list: List[str] = []
        if isinstance(to, (list, tuple, set)):
            target_list = [str(x).strip() for x in to if str(x).strip()]
        elif isinstance(to, str) and to.strip():
            target_list = [x.strip() for x in to.split(",") if x.strip()]

        # 留空时回退到默认 openid (也支持逗号分隔)
        if not target_list and self.default_openid:
            target_list = [x.strip() for x in self.default_openid.split(",") if x.strip()]

        if not target_list:
            return WeChatResult(
                success=False,
                err_code=-1,
                err_msg="未指定接收人 openid，且未配置默认 openid",
            )

        # 构建模板数据体
        if not template_data:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            template_data = {
                "first": {"value": title, "color": "#173177"},
                "keyword1": {"value": event_type, "color": "#555555"},
                "keyword2": {"value": content or "无", "color": "#333333"},
                "keyword3": {"value": content or "无", "color": "#333333"},
                "keyword4": {"value": now_str, "color": "#999999"},
                "remark": {"value": remark, "color": "#888888"},
            }

        url_target = click_url if click_url is not None else self.default_url

        # 单个目标直接发送
        if len(target_list) == 1:
            body = {
                "touser": target_list[0],
                "template_id": active_template_id,
                "url": url_target,
                "data": template_data,
            }
            return await self._do_send_with_retry(body)

        # 多个目标并发投递
        tasks = [
            self._do_send_with_retry({
                "touser": uid,
                "template_id": active_template_id,
                "url": url_target,
                "data": template_data,
            })
            for uid in target_list
        ]
        results = await asyncio.gather(*tasks)
        success_count = sum(1 for r in results if r.success)
        msg_ids = [r.msg_id for r in results if r.msg_id]
        return WeChatResult(
            success=(success_count > 0),
            msg_id=",".join(msg_ids),
            err_code=0 if success_count == len(target_list) else -5,
            err_msg=f"已成功推送至 {success_count}/{len(target_list)} 个指定用户",
            raw_response={"all_results": [r.__dict__ for r in results]},
        )

    async def send_job_alert(
        self,
        unit: str,
        post: str,
        enroll_status: str,
        change_time: Optional[str] = None,
        remark: str = "点击卡片可查看官方岗位详情",
        title: str = "【招考变动】岗位报名人数发生变化！",
        to: Optional[Any] = None,
        click_url: Optional[str] = None,
        template_id: Optional[str] = None,
    ) -> WeChatResult:
        """
        发送专属岗位变动提醒消息（完美对齐专属模板）
        """
        now_str = change_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        template_data = {
            "first": {"value": title, "color": "#173177"},
            "keyword1": {"value": unit, "color": "#333333"},
            "keyword2": {"value": post, "color": "#1E88E5"},
            "keyword3": {"value": enroll_status, "color": "#D32F2F"},
            "keyword4": {"value": now_str, "color": "#666666"},
            "remark": {"value": remark, "color": "#888888"},
        }
        return await self.send(
            title=title,
            to=to,
            click_url=click_url,
            template_data=template_data,
            template_id=template_id,
        )

    def send_job_alert_sync(
        self,
        unit: str,
        post: str,
        enroll_status: str,
        change_time: Optional[str] = None,
        remark: str = "点击卡片可查看官方岗位详情",
        title: str = "【招考变动】岗位报名人数发生变化！",
        to: Optional[Any] = None,
        click_url: Optional[str] = None,
    ) -> WeChatResult:
        """同步发送专属岗位变动提醒消息"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        coro = self.send_job_alert(
            unit=unit,
            post=post,
            enroll_status=enroll_status,
            change_time=change_time,
            remark=remark,
            title=title,
            to=to,
            click_url=click_url,
        )

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

    async def _do_send_with_retry(self, body: Dict[str, Any]) -> WeChatResult:
        """底层 HTTP 发送并在 access_token 过期时自动重试"""
        try:
            token = await self.get_access_token()
            url = f"{self.SEND_URL}?access_token={token}"

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                result = resp.json()

            # 若微信返回 token 无效或过期（40001, 40014, 42001），强制刷新并重试一次
            errcode = result.get("errcode", 0)
            if errcode in (40001, 40014, 42001):
                logger.warning("微信 access_token 过期 (errcode=%s)，尝试强制刷新重试", errcode)
                token = await self.get_access_token(force=True)
                url = f"{self.SEND_URL}?access_token={token}"
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=body)
                    resp.raise_for_status()
                    result = resp.json()
                errcode = result.get("errcode", 0)

            if errcode == 0:
                msg_id = str(result.get("msgid", ""))
                logger.info("微信模板消息推送成功: msg_id=%s, touser=%s", msg_id, body.get("touser"))
                return WeChatResult(
                    success=True,
                    msg_id=msg_id,
                    err_code=0,
                    raw_response=result,
                )

            errmsg = result.get("errmsg", "未知错误")
            logger.warning("微信模板消息推送失败: [%s] %s", errcode, errmsg)
            return WeChatResult(
                success=False,
                err_code=errcode,
                err_msg=errmsg,
                raw_response=result,
            )

        except httpx.TimeoutException:
            logger.error("请求微信推送接口超时")
            return WeChatResult(success=False, err_code=-3, err_msg="请求微信接口超时")
        except Exception as e:
            logger.error("微信推送发生异常: %s", str(e))
            return WeChatResult(success=False, err_code=-4, err_msg=str(e))

    def send_sync(
        self,
        title: str,
        content: str = "",
        to: Optional[str] = None,
        event_type: str = "系统通知",
        remark: str = "",
        click_url: Optional[str] = None,
        template_data: Optional[Dict[str, Any]] = None,
    ) -> WeChatResult:
        """
        同步发送方法，适合在无协程环境（如独立脚本或同步任务）中直接调用。
        内置事件循环探测与线程池适配，安全避免'loop already running'异常。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        coro = self.send(
            title=title,
            content=content,
            to=to,
            event_type=event_type,
            remark=remark,
            click_url=click_url,
            template_data=template_data,
        )

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)
