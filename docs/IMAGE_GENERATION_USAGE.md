# Flow2API 图片生成测试使用说明

本说明用于在本地运行 Flow2API，并测试 Google Flow 图片生成能力。当前文档只覆盖图片生成；视频和 Omni 能力可以后续再单独配置。

## 1. 准备环境

- Python 3.8 或更高版本。
- 可以访问 `https://labs.google/fx/zh/tools/flow` 的浏览器登录态。
- 如果本机直连 Google 会超时，需要先开启本机代理，例如 `http://127.0.0.1:7897`。

Windows 本地启动示例：

```powershell
cd C:\Users\10339\Desktop\codex\flow2api
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

服务启动后访问：

- 管理页：`http://127.0.0.1:8000`
- 测试页：`http://127.0.0.1:8000/test`
- 健康检查：`http://127.0.0.1:8000/health`

默认管理账号来自 `config/setting_example.toml`：

- 用户名：`admin`
- 密码：`admin`

首次登录后建议在管理页修改默认密码。

## 2. 登录 Flow 并准备项目

用 Chrome 打开调试端口 9223：

```powershell
& "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9223 `
  --user-data-dir="$env:USERPROFILE\flow2api-chrome-9223"
```

然后在这个 Chrome 窗口访问：

```text
https://labs.google/fx/zh/tools/flow
```

登录 Google 账号，并进入一个 Flow 项目页面。项目页面地址通常包含：

```text
/project/<project-id>
```

这个 `<project-id>` 后面导入 Token 时会用到。

## 3. 配置代理

如果本机可以直连 Google，可以跳过本节。

如果命令行请求 Google 超时，但浏览器可以正常打开 Flow，通常说明浏览器走了系统代理，而 Flow2API 后端没有走代理。可以在管理页配置：

- 请求代理：开启
- 请求代理地址：`http://127.0.0.1:7897`
- 媒体代理：开启
- 媒体代理地址：`http://127.0.0.1:7897`
- 验证码浏览器代理：开启
- 验证码浏览器代理地址：`http://127.0.0.1:7897`

也可以用下面的方式快速检查代理是否可用：

```powershell
curl.exe -I -L --max-time 20 --proxy http://127.0.0.1:7897 https://labs.google/auth/session
```

能返回 HTTP 响应即可。

## 4. 配置验证码模式

进入管理页后，将验证码方式设置为：

```text
personal
```

本地单账号图片测试建议：

- `browser_count`: `1`
- `personal_project_pool_size`: `1`
- `personal_max_resident_tabs`: `2`
- `personal_idle_tab_ttl_seconds`: `600`

这样可以减少首次测试时自动创建额外 Flow 项目的概率。

保存后检查运行状态，管理页应显示 personal 浏览器环境已就绪。

## 5. 导入 Flow Token

在 Chrome 9223 的 Flow 登录态中获取：

- `__Secure-next-auth.session-token`
- 当前 Flow 项目 URL 中的 `project_id`

在管理页添加 Token：

- ST：填写 `__Secure-next-auth.session-token` 的值。
- Project ID：填写当前 Flow 项目的 UUID。
- 图片能力：开启。
- 视频能力：如果只测试图片，可以关闭。
- Token 代理：如果本机需要代理，填写 `http://127.0.0.1:7897`。
- 打码代理：如果本机需要代理，填写 `http://127.0.0.1:7897`。
- 协议模式：使用 `session`。
- 自动刷新：开启。

导入后访问健康检查：

```powershell
curl.exe http://127.0.0.1:8000/health
```

应看到：

```json
{
  "has_active_tokens": true,
  "active_tokens": 1,
  "captcha_method": "personal"
}
```

不要把 ST、AT、API Key 或 Google 账号信息提交到仓库，也不要贴到公开 issue。

## 6. 使用测试页生图

打开：

```text
http://127.0.0.1:8000/test
```

填写：

- API Key：默认是 `han1234`，以管理页当前配置为准。
- 服务地址：`http://127.0.0.1:8000`
- 模型：例如 `gemini-3.1-flash-image-landscape`
- 提示词：输入要生成的图片描述。

点击“生成图片”。正常情况下日志会依次显示：

```text
图片生成任务已启动
初始化生成环境...
正在进行打码验证并提交图片生成请求...
```

完成后会在结果区域显示生成图片。

## 7. 使用 API 生图

PowerShell 示例：

```powershell
$body = @{
  model = "gemini-3.1-flash-image-landscape"
  stream = $true
  messages = @(
    @{
      role = "user"
      content = "一只可爱的橘猫趴在窗台上晒太阳，窗外是樱花盛开的春天"
    }
  )
} | ConvertTo-Json -Depth 10

Invoke-WebRequest `
  -Uri "http://127.0.0.1:8000/v1/chat/completions" `
  -Method POST `
  -Headers @{ Authorization = "Bearer han1234" } `
  -ContentType "application/json" `
  -Body $body
```

也可以使用 OpenAI 兼容客户端，将 base URL 设置为：

```text
http://127.0.0.1:8000/v1
```

## 8. 常见问题

### `has_active_tokens` 是 `false`

说明没有可用 Token。检查：

- ST 是否过期。
- Token 是否导入成功。
- AT 是否刷新成功。
- Token 图片能力是否启用。

### 导入 Token 时请求超时

多数情况下是后端没有走代理。先确认代理端口可用，再在管理页启用请求代理、媒体代理和验证码浏览器代理。

### 测试页提示“没有可用的 Token”

进入管理页确认至少有一个 active Token，并且图片能力已开启。

### personal 打码不稳定

检查：

- 代理是否和 Flow 登录浏览器使用同一出口。
- Chrome 9223 中 Flow 是否仍保持登录。
- `personal_project_pool_size` 是否过大。
- 本机是否有多个代理或 VPN 冲突。

### 图片链接过期

默认返回的是官方图片链接，链接带有效期。如果需要长期访问，可以在管理页开启文件缓存并配置缓存访问地址。
