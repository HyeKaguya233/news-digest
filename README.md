# 每日热榜简报 → QQ / 微信 / Windows

在 GitHub 服务器上每天定时抓取公开热榜，经过去重、来源过滤和可选的 AI 评审后，
生成一份中文简报，推送到 QQ、微信、iPhone（Bark），并写入 `latest.json` 供
Windows 托盘客户端和 AI Skill 使用。

部署完成后**不需要你的电脑开机，也不用一直运行 Codex**。GitHub Actions 负责每天
北京时间 **10:30** 自动执行；Windows 客户端只负责读取并弹本机通知。

仓库地址：<https://github.com/HyeKaguya233/news-digest>

## 简报内容

| 板块 | 数据源 | 默认内容 |
| --- | --- | --- |
| 🎯 你的推荐 | 按第一位读者画像搜索 Google News | 技术、自动驾驶、影像、AI 工程化，精选 6 条 |
| ✨ TA 的推荐 | 按第二位读者画像搜索 Google News | 影视/后期、医疗、时尚、医保社保、旅游，精选 8 条 |
| 🌍 海外要闻 | Google News RSS（WORLD，中文） | 国际头条 6 条 |
| 🔥 微博热搜 | weibo 接口 → 今日热榜(tophub) → vvhan | Top 10 |
| 🐦 X 全球趋势 | trends24.in（美国榜）→ getdaytrends | Top 10 |
| 💻 GitHub Trending | github.com/trending（daily） | Top 8，含当日新增 star |
| 📣 Facebook / Meta 动态 | about.fb.com 官方 RSS | 官方新闻 4 条 |
| 🇨🇳 中文热榜 | 知乎接口 → 今日热榜知乎榜 → 百度热搜 | Top 8 |
| 💬 微信热文 | 自备 RSS（可选） | 配置 `WECHAT_RSS_URL` 后出现 |

微信公众号没有稳定公开热榜接口，因此微信热文需要接入新榜、WeRSS、RSSHub 等
授权或自建 RSS。Facebook 已取消公开 Trending 榜单，这里使用 Meta 官方新闻替代。

原始链接始终保留：QQ/纯文本、Markdown、`latest.json` 和 Windows 客户端中的每条
内容都带来源链接。

## 工作原理

1. `fetch_news.py` 独立抓取各板块；单个源失败不会阻断其他板块。
2. 规则层做完全去重、跨板块近似去重、下载站/广告标题过滤，并保留来源名。
3. 配置模型 key 后，AI 评审只从已抓取候选中挑选、合并和排序，返回 3–5 条
   `highlights`、120–220 字 `briefing` 以及各板块说明。
4. 模型返回的 URL 必须与输入白名单完全一致，否则该条直接丢弃；结果过少或调用
   失败时自动回退到规则版，避免整期漏推。
5. 同一份内容分别渲染成 Qmsg 纯文本、Bark 纯文本、PushPlus/Server酱 Markdown，
   并写入 `latest.json`；Actions 成功后再把该 JSON 提交回仓库。
6. Windows 客户端轮询 `latest.json`，发现新一期后弹出系统通知；AI Skill 读取同一
   文件生成双人日报。

## 文件结构

```text
.
├── .github/workflows/
│   ├── daily.yml                 # 每天 10:30 北京时间抓取、推送、更新 latest.json
│   └── keepalive.yml             # 每月保活，降低长期无提交导致定时任务暂停的风险
├── fetch_news.py                 # 抓取、规则清洗、双画像、AI 评审、推送、Feed 输出
├── requirements.txt              # 服务端依赖
├── latest.json                   # Actions 生成的机器可读日报（不要手改）
├── README.md                     # 本文件
├── AGENTS.md                     # 给后续 AI/开发者维护本仓库的约束
├── skills/daily-news-digest/
│   └── SKILL.md                  # 双人日报整理 Skill
└── desktop/
    ├── news_tray.py              # Windows 托盘客户端源码
    ├── requirements.txt
    ├── build.ps1                 # 一键构建单文件 EXE
    ├── NewsDigest.spec           # PyInstaller 配置
    ├── assets/news.ico
    ├── dist/NewsDigest.exe       # 可直接转发的 Windows x64 单文件客户端
    ├── 使用说明.txt              # 普通用户说明
    └── README.md                 # 客户端开发和数据契约
```

## 仓库密钥与变量

只把密钥放在 **Settings → Secrets and variables → Actions**，不要写进代码、Issue、
聊天记录或 `latest.json`。

### Secrets

| 名称 | 用途 |
| --- | --- |
| `QMSG_KEY` | Qmsg酱，推送 QQ；免费版有内容审核，时政内容可能被拦截 |
| `BARK_KEY` | Bark，推送 iPhone；推荐作为稳定兜底通道 |
| `PUSHPLUS_TOKEN` | PushPlus，推送微信 |
| `SERVERCHAN_KEY` | Server酱，推送微信；可选 |
| `DEEPSEEK_API_KEY` | DeepSeek key，启用 AI 评审与 `briefing`；也可用 `DS` |
| `LLM_API_KEY` | 其他 OpenAI 兼容服务的 key；可选 |

### Variables

| 名称 | 默认/示例 | 用途 |
| --- | --- | --- |
| `WECHAT_RSS_URL` | `https://.../wechat.xml` | 微信热文 RSS；可选 |
| `USER_PROFILE` | 职业、项目、长期关注方向 | 第一位读者画像 |
| `PARTNER_PROFILE` | 兴趣、信息偏好 | 第二位读者画像 |
| `INTEREST_QUERIES` | `autonomous driving, computer vision, DJI` | 第一位读者的搜索词，最多 8 个 |
| `PARTNER_INTEREST_QUERIES` | `电影 电视剧, 多发性硬化, 医保 社保` | 第二位读者的搜索词，最多 8 个 |
| `RECOMMEND_TITLE` | `你的推荐` | 第一栏标题 |
| `PARTNER_RECOMMEND_TITLE` | `TA 的推荐` | 第二栏标题 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容 API 地址 |
| `LLM_MODEL` | `deepseek-chat` | 评审模型名 |

搜索词可以用英文逗号、中文逗号或 `|` 分隔。两栏必须保持独立：第一位读者偏技术和
影像，第二位读者偏影视/后期、医疗、时尚、医保社保和旅游，不能把两栏合并成泛资讯。

## 部署步骤

1. 在 Bark、Qmsg酱或 PushPlus 中至少准备一个推送通道。
2. 在 GitHub 打开本仓库，进入 **Settings → Secrets and variables → Actions**。
3. 在 **Secrets** 中添加推送 key；如果要用 AI 评审，再添加 `DEEPSEEK_API_KEY`。
4. 在 **Variables** 中按需添加画像、搜索词、标题和 `WECHAT_RSS_URL`。
5. 打开 **Actions → daily-news-digest → Run workflow**，`query` 留空，手动跑一次。
6. 成功后会收到推送，仓库里的 `latest.json` 也会由 Bot 自动更新。

之后每天北京时间 10:30 自动运行。GitHub 高峰时段可能延迟几分钟到几十分钟。

## 手动搜索

**Actions → daily-news-digest → Run workflow**，在 `query` 中填写关键词。此时不会
抓常规热榜，只搜索该关键词；所有已配置的推送通道都会收到搜索简报，但
`latest.json` 不会更新，避免一次手动搜索覆盖当天的每日简报。

## AI 日报 Skill

Skill 位于 `skills/daily-news-digest/SKILL.md`，用途是把 `latest.json` 整理成适合两位
读者共同阅读的中文日报。它会：

- 保留 A（技术与影像）和 B（影视、医疗与生活）两个独立栏目；
- 保留每条原始链接和来源；
- 合并同一事件，剔除广告、下载站和标题党；
- 医疗内容区分研究与已批准治疗，不给诊断或治疗建议；
- 医闹内容采用中性、可核验的措辞；
- 指出数据缺失，而不是编造内容。

在支持 Skill 的 Codex 环境中，把 `skills/daily-news-digest` 复制到
`%USERPROFILE%\.codex\skills\`（macOS/Linux 为 `~/.codex/skills/`），然后新开一个
会话并说「用 daily-news-digest 整理今天的日报」。Skill 会优先读取本地
`latest.json`，没有本地文件时读取仓库 Raw 地址。

## Windows 通知客户端

直接双击 `desktop/dist/NewsDigest.exe`。它会打开完整简报并常驻托盘，默认每 5 分钟
检查一次新的 `latest.json`，有新一期时弹出 Windows 系统通知；默认开机自启动。

- 默认订阅 GitHub Raw；访问失败时回退到 jsDelivr。
- 配置、状态和日志在 `%APPDATA%\NewsDigest\`。
- 首次运行可能触发 SmartScreen；未签名程序可选择「更多信息 → 仍要运行」。
- 客户端不包含 Bark、Qmsg、DeepSeek 等任何服务端密钥。
- 托盘菜单可以立即检查、打开简报或退出；只允许单实例运行。

构建方法见 `desktop/build.ps1` 和 `desktop/README.md`。

## 本地运行与测试

### PowerShell（Windows）

```powershell
python -m pip install -r requirements.txt

# 只抓取和打印，不推送、不写 Feed
$env:DRY_RUN='1'
python fetch_news.py

# 生成 latest.json，但不推送
$env:DRY_RUN='1'
$env:FEED_OUT='latest.json'
python fetch_news.py

# 真实推送一条测试消息
$env:DRY_RUN='0'
$env:QMSG_KEY='你的 Qmsg key'
python fetch_news.py
```

### Bash（Linux/macOS）

```bash
pip install -r requirements.txt
DRY_RUN=1 python fetch_news.py
DRY_RUN=1 FEED_OUT=latest.json python fetch_news.py
QMSG_KEY=你的key python fetch_news.py
```

本地使用其他 OpenAI 兼容模型时，设置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。

### 构建 Windows EXE

```powershell
cd desktop
.\build.ps1
```

输出为 `desktop\dist\NewsDigest.exe`。调试时可用 `python news_tray.py --once` 只检查
一次；常驻运行时不要直接读取真实用户目录做自动测试，建议把 `APPDATA` 指向临时目录。

## `latest.json` 数据契约

顶部字段包括 `date`、`generated_at`、`title`、`briefing`、`highlights`、`sections`、
`skipped`。每个板块包含 `key`、`icon`、`title`、`items`；每条 item 包含 `title`、
`url`、`note`、`source`。Windows 客户端和 Skill 都必须兼容旧版缺少 `briefing` 或
`source` 的文件。

`latest.json` 由 Actions 生成并提交，不要手工编辑；手工修改会在下一次工作流运行时被
覆盖，也容易让 Windows 客户端与推送内容不一致。

## 修改时间

编辑 `.github/workflows/daily.yml` 中的 cron。GitHub Actions 使用 **UTC**：

| 北京时间 | cron |
| --- | --- |
| 07:30 | `30 23 * * *` |
| 09:00 | `0 1 * * *` |
| 10:30 | `30 2 * * *` |

## 常见问题

- **到点没收到？** 先看 Actions 是否成功，再看日志中的通道结果；GitHub 定时触发
  本身可能延迟。
- **Qmsg 被拦截？** 免费版会审核内容；保留 Bark/PushPlus 作为备用通道。
- **某个板块缺了？** 数据源改版、限流或网络失败；日志会写原因，其他板块不受影响。
- **AI 评审失败？** 自动回退到规则版；检查 key、余额、`LLM_BASE_URL` 和模型名。
- **电脑关机还能运行吗？** 能。抓取和推送在 GitHub 上完成；只有 Windows 本机通知
  需要客户端运行。
- **如何确保密钥不泄露？** 只放 GitHub Secrets；不要把真实 key 发给他人或写进文件。
