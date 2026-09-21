"""
监控任务命令行运行入口
支持按任务调度或执行单次巡检。
"""

import sys
import asyncio
import logging
import argparse

from core.task_manager import task_manager
import tasks  # 触发任务插件目录自动扫描与注册

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("monitor.cli")


def main():
    parser = argparse.ArgumentParser(description="多任务监控调度器命令行工具")
    parser.add_argument(
        "--task",
        type=str,
        default="all",
        help="指定执行的任务 ID (例如: jd_offer_3jobs)，默认: all",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="仅执行单次检测后退出",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有已注册的监控任务",
    )
    args = parser.parse_args()

    # 1. 列表展示
    if args.list:
        print("=" * 60)
        print("          系统当前注册的监控任务列表")
        print("=" * 60)
        for t in task_manager.get_all_summaries():
            status_text = "已启用" if t["enabled"] else "未启用"
            print(f"- 任务ID: {t['task_id']:<20} 名称: {t['task_name']:<25} 周期: {t['interval_seconds']}s [{status_text}]")
        print("=" * 60)
        return

    # 2. 单次执行
    if args.once:
        async def run_once_async():
            if args.task == "all":
                print("[*] 正在单次执行所有已启用的任务...")
                for tid, task in task_manager.tasks.items():
                    if task.enabled:
                        res = await task.run_once()
                        print(f"    - [{tid}] 执行结果: {'成功' if res['success'] else '失败'} (耗时 {res.get('elapsed_ms')}ms)")
            else:
                task = task_manager.get_task(args.task)
                if not task:
                    print(f"[ERROR] 未找到任务 ID: {args.task}")
                    sys.exit(1)
                print(f"[*] 正在单次执行任务 [{args.task}]...")
                res = await task.run_once()
                print(f"[*] 执行结果: {res}")

        asyncio.run(run_once_async())
        return

    # 3. 常驻后台守护运行
    try:
        print(f"[*] 启动任务调度中心 (运行模式: {args.task})... 按 Ctrl+C 终止")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(task_manager.start_all())
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n收到退出指令，正在平稳停止所有监控任务...")
        task_manager.stop_all()


if __name__ == "__main__":
    main()
