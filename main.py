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
from core.plugin_manager import plugin_manager
from core.contact_manager import contact_manager
import tasks  # 触发任务目录自动扫描与发现
from notifier import send_job_alert, format_enroll_status
from core.auth import (
    COOKIE_NAME,
    verify_credentials,
    create_session_token,
    get_current_user,
    require_auth,
    LoginRequiredException,
)

from core.snapshot_manager import snapshot_manager

logger = logging.getLogger("web")
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 自适应兼容 Starlette / FastAPI 新旧版本的 TemplateResponse 签名差异
_original_template_response = templates.TemplateResponse


def _safe_template_response(*args, **kwargs):
    """
    自适应兼容 Starlette 新旧版本的 TemplateResponse:
    - 旧版签名: TemplateResponse(name, context, status_code=200, ...)
    - 新版签名 (Starlette >= 0.36): TemplateResponse(request=request, name=name, context=context, status_code=200, ...)
    """
    if len(args) >= 1 and isinstance(args[0], str):
        name = args[0]
        context = args[1] if len(args) > 1 else kwargs.get("context", {})
        request = context.get("request") or kwargs.get("request")
        status_code = kwargs.get("status_code", 200)
        if len(args) > 2:
            status_code = args[2]

        try:
            # 优先尝试新版签名
            if request is not None:
                return _original_template_response(
                    request=request, name=name, context=context, status_code=status_code
                )
        except TypeError:
            pass
        # 回退旧版签名
        return _original_template_response(name, context, status_code=status_code)

    return _original_template_response(*args, **kwargs)


templates.TemplateResponse = _safe_template_response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理器：管理后台任务调度中枢启动与平稳退出"""
    logger.info("--> 正在扫描与载入脚本插件仓库...")
    plugin_manager.reload()
    logger.info("--> 正在启动后台任务调度中心...")
    # 启动时执行一次过期快照静默清理
    snapshot_manager.cleanup_all_expired()
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


# ------ 移动端专属时效快照公开路由 (免登直达，微信内置浏览器秒开) ------

@app.get("/s/{snapshot_id}", response_class=HTMLResponse)
async def view_snapshot(request: Request, snapshot_id: str):
    """
    移动端时效快照查看页面
    专供微信客户端用户免登查看。默认 24 小时有效期，过期自动从磁盘物理销毁。
    """
    record = snapshot_manager.get_snapshot(snapshot_id)
    if not record:
        return templates.TemplateResponse(
            "snapshots/expired.html",
            {"request": request, "expire_at": None},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if record.get("expired"):
        return templates.TemplateResponse(
            "snapshots/expired.html",
            {"request": request, "expire_at": record.get("expire_at")},
            status_code=status.HTTP_410_GONE,
        )

    task_id = record.get("task_id", "")
    data = record.get("data", {})

    if task_id == "teach_fee_daily_report":
        return templates.TemplateResponse(
            "snapshots/teach_fee_report.html",
            {
                "request": request,
                "snapshot": record,
                "metrics": data,
            },
        )
    else:
        return templates.TemplateResponse(
            "snapshots/job_detail.html",
            {
                "request": request,
                "snapshot": record,
                "job": data,
            },
        )


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


def get_available_panels() -> List[Dict[str, Any]]:
    """
    动态扫描 templates/panels/ 目录下与已注册任务同名的前端插件面板
    实现真正的即插即用：只需放入 {task_id}.html 即可自动挂载到 Web 界面
    """
    panels_dir = BASE_DIR / "templates" / "panels"
    available = []
    if not panels_dir.exists():
        return available

    for task in task_manager.tasks.values():
        panel_file = panels_dir / f"{task.task_id}.html"
        if panel_file.is_file():
            available.append({
                "task_id": task.task_id,
                "task_name": task.task_name,
                "template_name": f"panels/{task.task_id}.html",
            })
    return available


# ------ 业务看板与 API 路由 (均受 require_auth 保护) ------

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request, current_user: str = Depends(require_auth)):
    """可视化控制看板首页"""
    panels = get_available_panels()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "current_user": current_user, "panels": panels},
    )


@app.get("/api/tasks", dependencies=[Depends(require_auth)])
async def get_all_tasks():
    """获取所有已注册任务的运行状态列表"""
    return JSONResponse(content={"tasks": task_manager.get_all_summaries()})


@app.post("/api/tasks/{task_id}/check-now", dependencies=[Depends(require_auth)])
async def trigger_task_now(task_id: str):
    """手动立即执行指定任务一次"""
    try:
        res = await task_manager.trigger_task(task_id, force=True)
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
    task_name: Optional[str] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    notify_openids: Optional[str] = None
    report_time: Optional[str] = None  # 定时任务时刻 (HH:MM)
    enable_notify: Optional[bool] = None
    notify_contact_ids: Optional[List[str]] = None
    plugin_config: Optional[Dict[str, Any]] = None


@app.post("/api/tasks/{task_id}/config", dependencies=[Depends(require_auth)])
async def update_task_config(task_id: str, payload: TaskConfigPayload):
    """在线更新指定任务的配置参数（即时热生效并写回文本）"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"success": False, "error": f"任务不存在: {task_id}"})
    try:
        new_config = task.update_config(
            task_name=payload.task_name,
            interval_seconds=payload.interval_seconds,
            enabled=payload.enabled,
            notify_openids=payload.notify_openids,
            report_time=payload.report_time,
            enable_notify=payload.enable_notify,
            notify_contact_ids=payload.notify_contact_ids,
            plugin_config=payload.plugin_config,
        )
        task_manager.sync_task_worker(task_id)
        return JSONResponse(content={"success": True, "config": new_config})
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


# ------ 微信通知通讯录人员管理 API ------

@app.get("/api/contacts", dependencies=[Depends(require_auth)])
async def get_contacts():
    """获取所有微信通知联系人列表"""
    return JSONResponse(content={"success": True, "contacts": contact_manager.list_contacts()})


class ContactCreatePayload(BaseModel):
    name: str
    openid: str
    remark: Optional[str] = ""


@app.post("/api/contacts", dependencies=[Depends(require_auth)])
async def create_contact(payload: ContactCreatePayload):
    """新增微信通知联系人"""
    try:
        contact = contact_manager.add_contact(
            name=payload.name,
            openid=payload.openid,
            remark=payload.remark or "",
        )
        return JSONResponse(content={"success": True, "contact": contact})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


class ContactUpdatePayload(BaseModel):
    name: Optional[str] = None
    openid: Optional[str] = None
    remark: Optional[str] = None


@app.put("/api/contacts/{contact_id}", dependencies=[Depends(require_auth)])
async def update_contact(contact_id: str, payload: ContactUpdatePayload):
    """修改联系人信息"""
    try:
        updated = contact_manager.update_contact(
            contact_id=contact_id,
            name=payload.name,
            openid=payload.openid,
            remark=payload.remark,
        )
        return JSONResponse(content={"success": True, "contact": updated})
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.delete("/api/contacts/{contact_id}", dependencies=[Depends(require_auth)])
async def delete_contact(contact_id: str):
    """删除指定联系人"""
    ok = contact_manager.delete_contact(contact_id)
    if not ok:
        return JSONResponse(status_code=404, content={"success": False, "error": "联系人不存在"})
    return JSONResponse(content={"success": True})


@app.post("/api/contacts/{contact_id}/test", dependencies=[Depends(require_auth)])
async def test_single_contact(contact_id: str):
    """向指定联系人发送微信握手测试消息"""
    contact = contact_manager.get_contact(contact_id)
    if not contact:
        return JSONResponse(status_code=404, content={"success": False, "error": "联系人不存在"})

    res = await send_job_alert(
        unit="WatchPatrol 任务监控系统",
        post=f"通讯录连通测试 · {contact['name']}",
        enroll_status="通道连通正常 | 测试通过",
        remark=f"这是一条来自任务调度平台的连通性握手测试消息。\n接收人: {contact['name']}\nOpenID: {contact['openid']}",
        title="【系统通知】微信推送接收人联调测试",
        to=[contact["openid"]],
    )
    return JSONResponse(
        content={
            "success": res.success,
            "msg_id": res.msg_id,
            "err_code": res.err_code,
            "err_msg": res.err_msg,
        }
    )


# ------ 脚本插件仓库与动态任务管理 API ------

@app.get("/api/plugins", dependencies=[Depends(require_auth)])
async def get_plugins():
    """获取所有已扫描加载的脚本插件元数据列表"""
    return JSONResponse(content={"success": True, "plugins": plugin_manager.list_plugins()})


@app.post("/api/plugins/reload", dependencies=[Depends(require_auth)])
async def reload_plugins():
    """重新扫描 plugins/ 目录加载新增的脚本文件"""
    plugin_manager.reload()
    return JSONResponse(content={"success": True, "plugins": plugin_manager.list_plugins()})


class CreateTaskPayload(BaseModel):
    task_name: str
    plugin_id: str
    task_type: Optional[str] = "interval"
    interval_seconds: Optional[int] = 60
    report_time: Optional[str] = "22:00"
    enabled: Optional[bool] = True
    enable_notify: Optional[bool] = True
    notify_contact_ids: Optional[List[str]] = None
    plugin_config: Optional[Dict[str, Any]] = None


@app.post("/api/tasks", dependencies=[Depends(require_auth)])
async def create_new_task(payload: CreateTaskPayload):
    """动态创建新的监控任务实例"""
    try:
        plugin = plugin_manager.get_plugin(payload.plugin_id)
        if not plugin:
            return JSONResponse(status_code=400, content={"success": False, "error": f"找不到指定的脚本插件: {payload.plugin_id}"})

        new_task = task_manager.create_task(
            task_name=payload.task_name,
            plugin_id=payload.plugin_id,
            task_type=payload.task_type or plugin.default_type,
            interval_seconds=payload.interval_seconds or plugin.default_interval,
            report_time=payload.report_time or plugin.default_report_time,
            enabled=True if payload.enabled is None else payload.enabled,
            enable_notify=True if payload.enable_notify is None else payload.enable_notify,
            notify_contact_ids=payload.notify_contact_ids or [],
            plugin_config=payload.plugin_config or {},
        )
        return JSONResponse(content={"success": True, "task": new_task.get_summary()})
    except Exception as e:
        logger.error("创建新任务失败: %s", e, exc_info=True)
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.delete("/api/tasks/{task_id}", dependencies=[Depends(require_auth)])
async def delete_task(task_id: str):
    """删除指定的自定义监控任务实例"""
    try:
        task_manager.delete_task(task_id)
        return JSONResponse(content={"success": True})
    except KeyError:
        return JSONResponse(status_code=404, content={"success": False, "error": f"任务不存在: {task_id}"})
    except ValueError as ve:
        return JSONResponse(status_code=400, content={"success": False, "error": str(ve)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/tasks/{task_id}/test-notify", dependencies=[Depends(require_auth)])
async def test_task_notify(task_id: str):
    """向指定任务当前配置的接收人发送一条测试通知"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"success": False, "error": f"任务不存在: {task_id}"})

    targets = task.get_notify_openids()
    if not targets:
        return JSONResponse(status_code=400, content={"success": False, "error": "该任务未配置任何有效的微信 openid"})

    if task_id == "teach_fee_daily_report":
        # 课时费任务专属通道测试（生成 24 小时专属移动端快照）
        tpl_id = os.getenv("TEACH_FEE_TEMPLATE_ID")
        mock_metrics = task.state.get("last_metrics") or {
            "new_users_today": 5,
            "ai_usage_today": 3,
            "ai_breakdown": {"feedback_generation": 2, "agent_chat": 1},
            "new_class_records_today": 90,
            "active_users_today": 30,
            "new_subscriptions_today": 0,
            "subscription_income_today": 0.0,
            "total_users": 4149,
        }
        snap_id = snapshot_manager.create_snapshot(
            task_id=task_id,
            title="课时费系统·运营日报测试快照",
            data=mock_metrics,
            ttl_hours=24,
        )
        snap_url = snapshot_manager.get_snapshot_url(snap_id)

        res = await send_job_alert(
            unit="课时费计算管理平台",
            post="运营日报通道连通性测试",
            enroll_status="测试通信正常 | 模板已绑定",
            remark="点击卡片可直接在微信中查阅专属移动端大屏\n本快照有效期 24 小时",
            title="【测试通知】课时费运营日报通道测试",
            to=targets,
            click_url=snap_url,
            template_id=tpl_id,
        )
    else:
        # 招考岗位任务专属通道测试（生成 24 小时专属移动端快照）
        tpl_id = os.getenv("WECHAT_TEMPLATE_ID")
        mock_job = {
            "id": "test_mock_job_1",
            "post_code": "QDG20260079",
            "unit": "信息支援部队 某部29",
            "post": "助理工程师",
            "quantity": 1,
            "post_num": 1,
            "subject": "计算机科学与技术、软件工程、人工智能、智能科学与技术、数据科学与大数据技术",
            "education": "硕士研究生",
            "station": "湖北 武汉",
            "tel": "027-58866836 / 15331277138",
            "do_job": "人工智能应用研究、模型算法开发、大数据挖掘等相关工作",
            "query_url": "http://211.166.6.109:9998/enroll/post/listVisitor?queryStr=QDG20260079",
        }
        snap_id = snapshot_manager.create_snapshot(
            task_id=task_id,
            title=f"{mock_job['post']} ({mock_job['post_code']}) 岗位变动快照",
            data=mock_job,
            ttl_hours=24,
        )
        snap_url = snapshot_manager.get_snapshot_url(snap_id)

        enroll_text = format_enroll_status(old_num=0, new_num=1, quota=1)
        res = await send_job_alert(
            unit=mock_job["unit"],
            post=f"{mock_job['post']} (代码: {mock_job['post_code']})",
            enroll_status=enroll_text,
            remark="点击卡片可直接在微信中查阅排版精美的岗位详情\n本快照有效期 24 小时",
            title="【招考变动】岗位报名人数发生变化！",
            to=targets,
            click_url=snap_url,
            template_id=tpl_id,
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
    """获取系统运行状态、任务列表与变动日志"""
    default_task = task_manager.get_task("jd_offer_3jobs") or (task_manager.tasks[0] if task_manager.tasks else None)
    state = default_task.state if default_task else {}
    interval = default_task.interval_seconds if default_task else 60

    # 聚合所有任务的持久化状态，支持所有插件面板读取对应数据
    task_states = {t.task_id: t.state for t in task_manager.tasks.values()}

    return JSONResponse(
        content={
            "current_user": current_user,
            "is_running": task_manager.is_running,
            "last_check_time": state.get("last_check_time", ""),
            "check_interval_seconds": interval,
            "jobs": state.get("jobs", {}),
            "history": state.get("history", []),
            "all_tasks": task_manager.get_all_summaries(),
            "task_states": task_states,
        }
    )


@app.post("/api/check-now", dependencies=[Depends(require_auth)])
async def trigger_check_now():
    """手动立即触发一次默认任务检测（兼容旧端点）"""
    default_task = task_manager.get_task("jd_offer_3jobs") or (task_manager.tasks[0] if task_manager.tasks else None)
    if not default_task:
        return JSONResponse(status_code=404, content={"success": False, "error": "当前未发现可用任务"})
    try:
        res = await default_task.run_once()
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


@app.post("/api/tasks/{task_id}/run-now", dependencies=[Depends(require_auth)])
async def trigger_task_run_now(task_id: str):
    """通用接口：手动立即执行任意指定任务"""
    task = task_manager.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"success": False, "error": f"任务不存在: {task_id}"})
    try:
        res = await task.run_once(force=True)
        return JSONResponse(content={"success": True, "result": res})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


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
    default_task = task_manager.get_task("jd_offer_3jobs") or (task_manager.tasks[0] if task_manager.tasks else None)
    if default_task:
        default_task.interval_seconds = payload.interval_seconds
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
