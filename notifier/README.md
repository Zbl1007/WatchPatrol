# 微信模板消息推送模块 (`notifier`)

基于微信公众平台（测试号 / 服务号）模板消息接口封装的高可用推送模块。

---

## 一、环境配置

在项目根目录下创建 `.env` 文件（可参考 `.env.example`）：

```bash
# 微信公众号/测试号参数
WECHAT_APP_ID=
WECHAT_APP_SECRET=
WECHAT_TEMPLATE_ID=
WECHAT_OPENID=
```

> **测试号 1 分钟开通指南**：
>
> 1. 打开微信测试号页面：[https://mp.weixin.qq.com/debug/cgi-bin/sandbox?t=sandbox/login](https://mp.weixin.qq.com/debug/cgi-bin/sandbox?t=sandbox/login)
> 2. 微信扫码登录，获取页面顶部的 `appID` 和 `appsecret`；
> 3. 点击「新增测试模板」，标题填“系统通知”，模板内容填写：
>    ```
>    {{first.DATA}}
>    类型：{{keyword1.DATA}}
>    内容：{{keyword2.DATA}}
>    时间：{{keyword3.DATA}}
>    {{remark.DATA}}
>    ```
>
>    保存后复制生成的「模板ID」；
> 4. 微信扫描右侧「测试号二维码」关注，并在下方的「用户列表」中复制自己的 `openid`。

---

## 二、使用示例

### 1. 异步调用（适用于 FastAPI、asyncio 监控主循环）

```python
from notifier import send_wechat_notice

async def alert_job_change():
    result = await send_wechat_notice(
        title="岗位报名人数变动",
        content="某招录单位 [助理工程师] 报名人数由 1 变更为 2",
        event_type="岗位动态",
        remark="请及时关注报名审核状态",
        click_url="http://example.com/..."  # 可选：点击直接跳转
    )
    if result.success:
        print("微信推送成功:", result.msg_id)
    else:
        print("微信推送失败:", result.err_msg)
```

### 2. 同步调用（适用于常规 Python 脚本、定时作业）

```python
from notifier import send_wechat_sync

result = send_wechat_sync(
    title="岗位报名人数变动",
    content="报名人数增加了 1 人",
)
print("推送状态:", result.success)
```

### 3. 自定义推送器实例（多接收人或多套配置）

```python
from notifier import WeChatNotifier

custom_notifier = WeChatNotifier(
    app_id="wx_custom_...",
    app_secret="sec_...",
    template_id="tpl_...",
    default_openid="openid_..."
)

# 动态发给指定 openid
await custom_notifier.send(
    title="测试通知",
    content="指定人员发送",
    to="oAnotherUserOpenid..."
)
```

---

## 三、返回数据结构 (`WeChatResult`)

| 属性             | 类型              | 说明                                                     |
| :--------------- | :---------------- | :------------------------------------------------------- |
| `success`      | `bool`          | 发送是否成功                                             |
| `msg_id`       | `Optional[str]` | 微信返回的消息 ID（成功时返回）                          |
| `err_code`     | `Optional[int]` | 错误代码（0 表示正常，负数为本地错误，正数为微信错误码） |
| `err_msg`      | `Optional[str]` | 错误详情文本                                             |
| `raw_response` | `dict`          | 微信官方 API 的原始返回 JSON                             |
