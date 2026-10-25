# NewsDigest Windows Client

Windows 托盘客户端：每 5 分钟读取仓库里的 `latest.json`，有新一期时发系统通知；
点击通知或托盘菜单可打开本地渲染的完整简报。

## Files

- `news_tray.py` — 客户端源码（托盘、轮询、HTML 渲染、通知、单实例、开机启动）
- `requirements.txt` — 客户端依赖
- `build.ps1` — 一键安装依赖并构建单文件 EXE
- `NewsDigest.spec` — PyInstaller 配置
- `assets/news.ico` — 应用图标
- `dist/NewsDigest.exe` — 已构建的 Windows x64 单文件版本
- `使用说明.txt` — 给普通用户看的运行说明

## Data contract

客户端只读取公开的 `latest.json`，不包含任何推送密钥。当前使用的字段：

- `date`, `generated_at`, `title`, `briefing`, `highlights`, `sections`, `skipped`
- `sections[].key`, `sections[].icon`, `sections[].title`, `sections[].items[]`
- `items[].title`, `items[].url`, `items[].note`, `items[].source`

`briefing` 会显示为“AI 日报总览”，`source` 会显示在每条说明后面；旧版 JSON
没有这两个字段也能正常运行。

## Run from source

```powershell
cd desktop
python -m pip install -r requirements.txt
python news_tray.py
```

调试单次检查（不常驻）：`python news_tray.py --once`。
开机自启动使用 `--minimized`，不会主动打开浏览器。

## Build

```powershell
cd desktop
.\build.ps1
```

输出：`desktop\dist\NewsDigest.exe`。单文件 EXE 首次运行会解压到临时目录，因此
启动时出现一个 PyInstaller 父进程和一个实际应用子进程属于正常现象。

## Runtime locations

- 配置/状态/日志：`%APPDATA%\NewsDigest\`
- 启动项：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- 订阅地址默认是 GitHub Raw，失败时回退 jsDelivr
- 可用 `NEWS_FEED_URL` 指向本地或镜像 JSON 做测试

## Safety

- 客户端只申请当前用户的开机启动权限，不需要管理员权限。
- 不保存 Bark、Qmsg、DeepSeek 等任何服务端密钥。
- 首次运行 SmartScreen 可能提示未签名；选择“更多信息 → 仍要运行”。
