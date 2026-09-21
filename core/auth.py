"""
轻量级管理员会话鉴权模块
基于标准库 HMAC-SHA256 签名 Cookie 实现，零外部依赖，防计时攻击与 XSS。
"""

import os
import time
import hmac
import base64
import hashlib
import secrets
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

# 确保读取 .env
load_dotenv()

COOKIE_NAME = "session_token"
DEFAULT_EXPIRE_DAYS = 7


def get_admin_credentials():
    username = os.getenv("ADMIN_USERNAME", "admin").strip()
    password = os.getenv("ADMIN_PASSWORD", "admin123456").strip()
    secret = os.getenv("SECRET_KEY", "jd_offer_secret_key_default").strip()
    return username, password, secret


def verify_credentials(input_user: str, input_pass: str) -> bool:
    """常量时间安全比对用户名和密码"""
    admin_user, admin_pass, _ = get_admin_credentials()
    user_ok = secrets.compare_digest(input_user.strip(), admin_user)
    pass_ok = secrets.compare_digest(input_pass.strip(), admin_pass)
    return user_ok and pass_ok


def create_session_token(username: str, expire_days: int = DEFAULT_EXPIRE_DAYS) -> str:
    """生成带签名的安全会话 Token"""
    _, _, secret = get_admin_credentials()
    expire_ts = int(time.time()) + (expire_days * 86400)
    payload = f"{username}:{expire_ts}"

    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    raw_token = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(raw_token.encode("utf-8")).decode("utf-8")


def verify_session_token(token: Optional[str]) -> Optional[str]:
    """验证会话 Token，合法返回 username，非法或过期返回 None"""
    if not token:
        return None

    try:
        raw_token = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = raw_token.split(":")
        if len(parts) != 3:
            return None

        username, expire_ts_str, signature = parts
        expire_ts = int(expire_ts_str)

        # 检查是否过期
        if time.time() > expire_ts:
            return None

        # 验证签名
        _, _, secret = get_admin_credentials()
        payload = f"{username}:{expire_ts}"
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if secrets.compare_digest(signature, expected_sig):
            return username
        return None
    except Exception:
        return None


def get_current_user(request: Request) -> Optional[str]:
    """从请求 Cookie 中获取当前登录用户"""
    token = request.cookies.get(COOKIE_NAME)
    return verify_session_token(token)


class LoginRequiredException(Exception):
    """未登录异常，供中间件或异常处理器捕获进行重定向或401拦截"""
    pass


async def require_auth(request: Request) -> str:
    """FastAPI 依赖项：要求必须已登录"""
    user = get_current_user(request)
    if not user:
        # 判断是页面访问还是 API 请求
        is_api = request.url.path.startswith("/api/")
        accept = request.headers.get("accept", "")
        if is_api or ("application/json" in accept and "text/html" not in accept):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="未授权访问，请先登录",
            )
        raise LoginRequiredException()
    return user
