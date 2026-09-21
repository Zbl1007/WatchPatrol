"""
任务监控平台 Web 服务主入口
基于 FastAPI 提供可视化看板、多任务统一调度与管理员身份安全认证。
"""

import os
import logging
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from core.task_manager import task_manager
import tasks  # 触发任务注册
from tasks import jd_offer_task
from notifier import send_job_alert, format_enroll_status
from core.auth import (
    COOKIE_NAME,
    verify_credentials,
    create_session_token,
    get_current_user,
    require_auth,
    LoginRequiredException,
)

logger = logging.getLogger("web")
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理器：管理后台任务调度中枢启动与平稳退出"""
    logger.info("--> 正在启动后台任务调度中心...")
    await task_manager.start_all()
    yield
    logger.info("<-- 正在停止后台任务调度中心...")
    task_manager.stop_all()
    logger.info("所有后台监控任务已安全退出。")


app = FastAPI(
    title="任务监控平台",
    description="轻量级插件化多任务监控系统，支持微信模板消息即时推送与安全会话鉴权",
    lifespan=lifespan,
)


# 未登录异常处理器：页面请求自动重定向至登录页
@app.exception_handler(LoginRequiredException)
async def login_required_handler(request: Request, exc: LoginRequiredException):
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=status.HTTP_302_FOUND)


# ------ 登录与注销路由 ------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """管理员登录界面"""
    current_user = get_current_user(request)
    if current_user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def do_login(request: Request):
    """处理管理员登录验证"""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
    else:
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))

    if not verify_credentials(username, password):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"success": False, "error": "管理员账号或密码错误"},
        )

    # 验证成功，签发会话 Token 并写入安全 HttpOnly Cookie
    token = create_session_token(username)
    response = JSONResponse(content={"success": True, "username": username})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=7 * 86400,  # 7天有效期
        httponly=True,
        samesite="lax",
    )
    logger.info("管理员 [%s] 登录成功，已分配安全会话", username)
    return response


@app.get("/logout")
async def do_logout():
    """退出登录并清除 Cookie"""
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key=COOKIE_NAME)
    return response


# ------ 业务看板与 API 路由 (均受 require_auth 保护) ------

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request, current_user: str = Depends(require_auth)):
    """可视化控制看板首页"""
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "current_user": current_user},
    )


@app.get("/api/tasks", dependencies=[Depends(require_auth)])
async def get_all_tasks():
    """获取所有已注册任务的运行状态列表"""
    return JSONResponse(content={"tasks": task_manager.get_all_summaries()})


@app.post("/api/tasks/{task_id}/check-now", dependencies=[Depends(require_auth)])
async def trigger_task_now(task_id: str):
    """手动立即执行指定任务一次"""
    try:
        res = await task_manager.trigger_task(task_id)
        return JSONResponse(content=res)
    except KeyError:
        return JSONResponse(status_code=404, content={"success": False, "error": f"任务不存在: {task_id}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.get("/api/tasks/{task_id}/config", dependencies=[Depends(require_auth)])
async def get_task_config(task_id: str):
    """获取指定任务的当前配置详情"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"success": False, "error": f"任务不存在: {task_id}"})
    return JSONResponse(content={"success": True, "config": task.get_config()})


class TaskConfigPayload(BaseModel):
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    notify_openids: Optional[str] = None


@app.post("/api/tasks/{task_id}/config", dependencies=[Depends(require_auth)])
async def update_task_config(task_id: str, payload: TaskConfigPayload):
    """在线更新指定任务的配置参数（即时热生效并写回文本）"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"success": False, "error": f"任务不存在: {task_id}"})
    try:
        new_config = task.update_config(
            interval_seconds=payload.interval_seconds,
            enabled=payload.enabled,
            notify_openids=payload.notify_openids,
        )
        return JSONResponse(content={"success": True, "config": new_config})
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.post("/api/tasks/{task_id}/test-notify", dependencies=[Depends(require_auth)])
async def test_task_notify(task_id: str):
    """向指定任务当前配置的接收人发送一条测试通知"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"success": False, "error": f"任务不存在: {task_id}"})

    targets = task.get_notify_openids()
    if not targets:
        return JSONResponse(status_code=400, content={"success": False, "error": "该任务未配置任何有效的微信 openid"})

    enroll_text = format_enroll_status(old_num=1, new_num=2, quota=1)
    res = await send_job_alert(
        unit="任务监控平台联调",
        post=f"测试通知 · {task.task_name}",
        enroll_status=enroll_text,
        remark="这是来自 Web 控制台对该任务接收人的通道测试",
        to=targets,
        click_url="http://211.166.6.109:9998/enroll/post/listVisitor?queryStr=QDG20260081",
    )
    return JSONResponse(
        content={
            "success": res.success,
            "msg_id": res.msg_id,
            "err_code": res.err_code,
            "err_msg": res.err_msg,
            "targets": task.get_masked_notify_targets(),
        }
    )


@app.get("/api/status", dependencies=[Depends(require_auth)])
async def get_status(current_user: str = Depends(require_auth)):
    """获取当前招考岗位监控任务的最新状态、最后更新时间与变动日志"""
    state = jd_offer_task.state
    return JSONResponse(
        content={
            "current_user": current_user,
            "is_running": task_manager.is_running,
            "last_check_time": state.get("last_check_time", ""),
            "check_interval_seconds": jd_offer_task.interval_seconds,
            "jobs": state.get("jobs", {}),
            "history": state.get("history", []),
            "all_tasks": task_manager.get_all_summaries(),
        }
    )


@app.post("/api/check-now", dependencies=[Depends(require_auth)])
async def trigger_check_now():
    """手动立即触发一次招考岗位检测（兼容旧端点）"""
    try:
        res = await jd_offer_task.run_once()
        changes = res.get("data", {}).get("changes", [])
        return JSONResponse(
            content={
                "success": res.get("success", False),
                "check_time": res.get("time"),
                "changes_count": len(changes),
                "changes": changes,
            }
        )
    except Exception as e:
        logger.error("手动执行检测异常: %s", e)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)},
        )


class SettingsPayload(BaseModel):
    interval_seconds: int


@app.post("/api/settings", dependencies=[Depends(require_auth)])
async def update_settings(payload: SettingsPayload):
    """调整当前主任务巡检周期秒数"""
    if payload.interval_seconds < 10:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "巡检周期不得低于 10 秒"},
        )
    jd_offer_task.interval_seconds = payload.interval_seconds
    return JSONResponse(
        content={"success": True, "interval_seconds": payload.interval_seconds}
    )


@app.post("/api/test-notify", dependencies=[Depends(require_auth)])
async def test_wechat_push():
    """在 Web 控制台一键测试微信推送通道"""
    enroll_text = format_enroll_status(old_num=1, new_num=2, quota=1)
    res = await send_job_alert(
        unit="重点招考单位",
        post="助理工程师 (代码: QDG20260081)",
        enroll_status=enroll_text,
        remark="这是来自 Web 控制台的主动联调测试",
        click_url="http://211.166.6.109:9998/enroll/post/listVisitor?queryStr=QDG20260081",
    )
    return JSONResponse(
        content={
            "success": res.success,
            "msg_id": res.msg_id,
            "err_code": res.err_code,
            "err_msg": res.err_msg,
        }
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8080))
    reload_enabled = os.getenv("RELOAD", "false").lower() in ("true", "1", "yes")
    print(f"[*] 启动任务监控平台 Web 应用: http://0.0.0.0:{port} (代码热重载: {'开启' if reload_enabled else '关闭'})")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload_enabled)
