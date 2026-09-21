#!/usr/bin/env python3
"""
微信消息推送模块测试脚本
用于验证凭据是否正确配置并测试接收微信消息
"""

import sys
from notifier import wechat_notifier, send_job_alert_sync, format_enroll_status


def main():
    print("==================================================")
    print("       微信公众号（测试号）模板消息推送测试        ")
    print("==================================================")

    # 1. 检查凭据配置状态
    configured = wechat_notifier.is_configured()
    print(f"[*] 配置状态检测: {'已配置完整' if configured else '未配置完整'}")
    print(f"    - WECHAT_APP_ID: {'已设置' if wechat_notifier.app_id else '未设置'}")
    print(f"    - WECHAT_APP_SECRET: {'已设置' if wechat_notifier.app_secret else '未设置'}")
    print(f"    - WECHAT_TEMPLATE_ID: {'已设置' if wechat_notifier.template_id else '未设置'}")
    print(f"    - WECHAT_OPENID: {'已设置 (' + wechat_notifier.default_openid[:4] + '***)' if wechat_notifier.default_openid else '未设置'}")

    if not configured or not wechat_notifier.default_openid:
        print("\n[!] 提示: 尚未配置完整的微信参数，请先在项目根目录下创建 .env 文件，并填入以下配置：")
        print("""
WECHAT_APP_ID=你的测试号appID
WECHAT_APP_SECRET=你的测试号appsecret
WECHAT_TEMPLATE_ID=你的测试模板ID
WECHAT_OPENID=接收通知的微信号openid
""")
        print("未配置测试完成（安全阻断，未抛异常）。")
        return

    # 2. 执行测试推送（使用专属岗位变动模板）
    print("\n[*] 正在向微信发送专属模板测试消息...")
    enroll_text = format_enroll_status(old_num=1, new_num=2, quota=1)
    result = send_job_alert_sync(
        unit="重点招考单位",
        post="助理工程师 (代码: QDG20260081)",
        enroll_status=enroll_text,
        remark="点击卡片可查看官方岗位详情",
        click_url="http://211.166.6.109:9998/enroll/post/listVisitor?queryStr=QDG20260081"
    )

    print(f"[*] 推送结果: {result}")
    if result.success:
        print("\n[SUCCESS] 微信消息发送成功！请在您的手机微信上查看测试号推送。")
    else:
        print(f"\n[FAIL] 微信消息发送失败！错误代码: {result.err_code}，原因: {result.err_msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
