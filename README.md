# WatchPatrol (巡哨) · 轻量多任务监控与告警平台

**WatchPatrol (巡哨)** 是一个**轻量级、免数据库、可扩展的现代化多任务监控与告警平台**。
既可全天候盯梢招考重点岗位报名动态，也能随时通过插件化架构无缝接入任意新的自定义监控脚本，由中心统一调度、Web 可视化管控并支持多接收人微信卡片消息直推。

---

## 一、架构设计

```text
WatchPatrol/
├── core/                    # 调度框架与安全鉴权
│   ├── auth.py              # Web Session Cookie 签名认证
│   ├── base_task.py         # 任务基类 (所有自定义监控均继承此类)
│   └── task_manager.py      # 任务管理与调度中心 (生命周期管理、独立协程调度)
├── tasks/                   # 业务任务插件目录 (即插即用)
│   ├── jd_offer_task.py     # 任务 1: 招录重点岗位报名监控 (默认启用)
│   ├── example_task.py      # 任务模版: 供后续编写新监控脚本参考复制
│   └── __init__.py          # 任务注册中心
├── notifier/                # 微信公众号/测试号推送模块 (所有任务全局复用)
│   ├── wechat.py            # 推送核心类与 Token 自动生命周期
│   └── __init__.py          # 导出 send_job_alert, send_wechat_notice 等便捷方法
├── data/                    # JSON 文本数据持久化 (纯文本，免外部数据库)
│   ├── jobs_state.json      # 招考岗位状态与变动日志
│   └── .gitkeep
├── templates/               # 现代化 Web 控制台与登录页面
│   ├── index.html           # 监控大屏、可折叠任务卡片、配置模态框
│   └── login.html           # 科技感后台登录界面
├── Dockerfile               # 生产级 Docker 镜像（内置国内源优化）
├── docker-compose.yml       # Docker Compose 一键编排文件
├── main.py                  # FastAPI Web 控制台与调度服务
└── requirements.txt         # 核心依赖清单
```

---

## 二、后续如何快速新增一个监控脚本？

未来如果您有任何新的监控需求（例如：笔试成绩发布、其他岗位轮询、面试名单公示等），只需 **两步**：

### 只需要一步：在 `tasks/` 目录下新建一个文件（如 `tasks/my_new_task.py`）

直接参考 `tasks/example_task.py` 的格式继承 `BaseTask`：

```python
import httpx
from core.base_task import BaseTask
from notifier import send_wechat_notice

class MyNewTask(BaseTask):
    task_id = "exam_result_monitor"          # 唯一标识
    task_name = "笔试成绩发布监控"            # 任务名称
    interval_seconds = 120                   # 独立周期：每 2 分钟执行一次
    enabled = True                           # 设为 True 即可随系统自启

    async def execute_check(self):
        # 1. 抓取目标网站
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://目标网站/api")
            data = resp.json()

        # 2. 判断是否变动并调用微信推送
        if data.get("published"):
            await send_wechat_notice(
                title="【重要通知】笔试成绩已发布！",
                content="官方成绩通道已正式开启，请尽快前往查分",
                event_type="考试成绩动态"
            )
            # 记录历史流水 (自动持久化)
            self.record_history({"event": "成绩发布", "status": "已提醒"})
        
        return {"status": "ok"}
```

**就这一步，搞定！无需修改 `__init__.py`！**
系统启动时会**自动扫描并注册** `tasks/` 目录下的所有任务脚本。
系统调度中心会自动为新任务分配独立的协程工作流，Web 控制台也会自动呈现该任务的运行状态卡片。

---

## 三、系统运行方式

### 1. 启动 Web 控制看板（推荐）

```bash
python3 main.py
```
- 访问地址：**`http://localhost:8080`**
- 默认管理员账号：`admin`，初始密码：`admin123456`（可在 `.env` 中随时修改）；
- **Web 在线动态配置**：
  - 点击任意任务卡片右上角的 **【⚙️ 配置】** 按钮；
  - 支持在线修改**巡检周期（秒）**、**专属微信接收人 openid（支持输入多个用逗号隔开）**以及**任务启用/暂停开关**；
  - 点击保存即时热生效，自动持久化至本地 JSON，服务重启依然保留；
  - 弹窗内支持“发送测试通知”一键验证该接收人能否正常接收微信卡片。

### 2. 命令行多任务管理

```bash
# 查看当前所有已注册的监控任务
python3 monitor.py --list

# 单次测试指定任务
python3 monitor.py --task jd_offer_3jobs --once

# 后台常驻运行调度器
python3 monitor.py
```
