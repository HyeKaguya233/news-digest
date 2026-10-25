# 每日热榜简报 → QQ / 微信

在 GitHub 的服务器上每天定时抓取公开热榜，生成一份中文简报并推送到你的 QQ 或微信。
部署完成后**不需要你的电脑开机**，也不用一直开着 Codex。

## 简报内容

| 板块 | 数据源 | 说明 |
| --- | --- | --- |
| 🌍 海外要闻 | Google News RSS（WORLD，中文） | 国际头条 6 条 |
| 🔥 微博热搜 | weibo 接口 → 今日热榜(tophub) → vvhan | Top 10 |
| 🐦 X 全球趋势 | trends24.in（美国榜）→ getdaytrends | Top 10 |
| 💻 GitHub Trending | github.com/trending（daily） | Top 8，含当日新增 star |
| 📣 Facebook / Meta 动态 | about.fb.com 官方 RSS | 官方新闻 4 条 |
| 🇨🇳 中文热榜 | 知乎接口 → 今日热榜知乎榜 → 百度热搜 | Top 8 |
| 💬 微信热文 | 你自己提供的 RSS（可选） | 见下方说明 |

> 说明：微信公众号热文没有公开接口，必须接入新榜、WeRSS 或 RSSHub 之类的授权/自建源。
> 配好 `WECHAT_RSS_URL` 后会自动多出一个「微信热文」板块。
> Facebook 已取消公开的 Trending 榜单，因此这里取 Meta 官方新闻作为替代。

## 文件结构

```
.
├── .github/workflows/daily.yml   # 定时任务（每天 08:00 北京时间）
├── fetch_news.py                 # 抓取 + 组装 + 推送
├── requirements.txt
└── README.md
```

## 部署（约 5 分钟）

### 1. 拿一个推送 key（选一个就行，也可以都配）

- **iPhone：Bark（推荐，免费、全文、无审核）** — App Store 搜 `Bark` 安装，首页会给一个
  `https://api.day.app/xxxxxxxx/` 地址，把最后那段 `xxxxxxxx` 填进 `BARK_KEY`。
- **QQ：Qmsg酱** — 打开 <https://qmsg.zendee.cn/>，QQ 登录 → 扫码加机器人好友 → 私聊发一次 API Key 完成绑定。
  注意：免费版有内容审核，**时政类新闻会被拦截**，不适合新闻摘要。
- **微信：PushPlus** — 打开 <https://www.pushplus.plus/>，微信扫码 → 实名认证 → 复制 token。
  免费版只显示标题摘要，正文要点链接看。

### 2. 建仓库

在 GitHub 新建一个 **Public** 仓库，例如 `news-digest`。

### 3. 上传文件

方式 A（最简单）：把本目录里的 `fetch_news.py`、`requirements.txt` 直接拖进仓库页面。

方式 B（推荐，能保留隐藏目录）：在仓库页点 **Add file → Create new file**，文件名填
`.github/workflows/daily.yml`，把工作流内容粘进去并提交；再重复一次上传另外两个文件。

### 4. 配置密钥

仓库 **Settings → Secrets and variables → Actions → New repository secret**：

| 名称 | 值 |
| --- | --- |
| `BARK_KEY` | Bark 的 key（只填地址最后那段） |
| `DEEPSEEK_API_KEY` | DeepSeek 的 key（可选，启用模型评审） |
| `QMSG_KEY` | Qmsg酱的 key（推送 QQ） |
| `PUSHPLUS_TOKEN` | PushPlus 的 token（推送微信） |

只想用一个通道就只加对应的那个。想加微信 RSS 源的话，在 **Variables** 里加 `WECHAT_RSS_URL`。

## 模型评审（可选）

不配 key 时，脚本只做规则清洗：完全去重、跨板块近似去重（相似度 ≥ 0.85）、广告词过滤，排序沿用各站榜单。

配置 `DEEPSEEK_API_KEY` 后会多一层模型评审：

- 合并同一事件的多条报道
- 剔除标题党、软文、无信息量的内容
- 按重要性重排，并给出 3 条「今日要点」
- 每条附一句 20 字以内的说明

模型输出的链接必须与输入完全一致，否则该条会被丢弃；调用失败或结果过少会自动回退到规则版，不会导致漏推。
默认模型 `deepseek-chat`，也可以用 Variables 里的 `LLM_MODEL` 换成别的（如 `deepseek-reasoner`）。

### 5. 先手动跑一次

**Actions → daily-news-digest → Run workflow**。等 1 分钟左右看日志，
成功的话 QQ / 微信会立刻收到一条简报。

### 6. 完成

之后每天北京时间 08:00 自动执行，无需任何操作。

## 改时间

编辑 `.github/workflows/daily.yml` 里的 `cron`，规则是 `分 时 * * *`，**UTC 时间**：

- 北京 07:30 → `30 23 * * *`
- 北京 08:00 → `0 0 * * *`
- 北京 09:00 → `0 1 * * *`

## 常见问题

- **到点没收到？** GitHub 定时任务在高峰期可能延迟几分钟到几十分钟，属于正常现象。
- **仓库 60 天没动过？** 仓库已内置 `keepalive.yml`，每月 1 日自动写入一次时间戳，避免 GitHub 因长期无提交而暂停定时任务。
- **某个板块缺了？** 脚本里每个源都是独立容错的，缺板块说明该站改版或被限流，日志里会写原因。
- **失败了会怎样？** 工作流变红，GitHub 会给你发邮件；如果全部通道都推送失败，任务也会标记为失败。
- **key 会泄露吗？** Secret 不会出现在代码和公开日志里，别把 key 直接写进文件即可。

## 本地调试

```bash
pip install -r requirements.txt
DRY_RUN=1 python fetch_news.py        # 只打印，不推送
QMSG_KEY=你的key python fetch_news.py  # 真推一条测试
```
