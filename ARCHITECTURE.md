# ARCHITECTURE.md

面向人类和 AI 协作者的项目说明。目标是：任何新会话只读这一份文件，就能安全地
修改、测试和发布，而不破坏既有约束。部署步骤见 `README.md`，硬性规则见 `AGENTS.md`。

## 1. 一句话

GitHub Actions 每天北京时间 10:30 抓取公开热榜 → 规则清洗 → 模型评审 → 推送到
iPhone / QQ / 微信，并把结构化结果写入 `latest.json`；Windows 客户端和 Codex Skill
消费同一份 JSON。

运行时不依赖任何常开机器，也不需要 Codex 在线。

## 2. 架构图

```
GitHub Actions (ubuntu-latest, cron 30 2 * * *)
        |
        v
  fetch_news.py
   |--- ThreadPoolExecutor 并发抓取 8~9 个板块（单源失败不影响其他）
   |--- apply_quality_rules()  跨板块近似去重 + 广告/低质过滤
   |--- shorten_sections()     长链接转 is.gd 短链（失败保留原链）
   |--- llm_review()           DeepSeek 兼容接口；产出 briefing/highlights/note
   |                            URL 必须命中输入白名单，否则丢弃该条
   |--- render_plain() / render_markdown()
   |
   |--> Bark / Qmsg / PushPlus / Server酱  (手机侧推送)
   `--> latest.json  --(Actions 提交回仓库)--> raw.githubusercontent.com
                                                    |
                             +----------------------+----------------------+
                             |                                             |
                    Windows 客户端 (轮询 300s)                    Codex Skill（按需）
                    弹系统通知 + digest.html                     生成中文双人日报
```

## 3. 文件职责

| 路径 | 作用 | 谁在跑 |
| --- | --- | --- |
| `fetch_news.py` | 抓取、清洗、评审、渲染、推送、写 feed | GitHub Actions |
| `.github/workflows/daily.yml` | 每天 10:30 定时 + 手动触发 | GitHub Actions |
| `.github/workflows/import-project.yml` | 手动构建 Windows EXE（显示名 `build-windows-client`，文件名是历史遗留） | GitHub Actions |
| `.github/workflows/keepalive.yml` | 保活，避免 60 天无提交导致定时任务被停用 | GitHub Actions |
| `latest.json` | 生成物，桌面端与 Skill 的唯一数据源 | Actions 自动提交 |
| `desktop/news_tray.py` | 托盘客户端：轮询、去重、弹通知、渲染 HTML | 用户电脑 |
| `desktop/build.ps1` / `NewsDigest.spec` | PyInstaller 打包 | 本地或 Actions |
| `desktop/dist/NewsDigest.exe` | 已构建客户端（Windows） | 用户电脑 |
| `skills/daily-news-digest/SKILL.md` | 把 `latest.json` 变成双人日报的 Skill | Codex |
| `AGENTS.md` | 项目硬约束（改代码前必读） | 人与 AI |
| `README.md` | 部署、变量、排障 | 人与 AI |

## 4. 一次运行的完整流程

1. `main()` 读取环境变量，计算 `title`（`每日简报 MM-DD`）。
2. `gather_sections()`：
   - 若 `SEARCH_QUERY` 非空 → 只抓 `search` 一个板块，走 `fetch_topic_search()`，不写 `latest.json`。
   - 否则并发抓 8 个固定板块，配置了 `WECHAT_RSS_URL` 时再加 `wechat`。
3. `apply_quality_rules()`：板块内保序，跨板块用 `difflib` 相似度 ≥ 0.85 去重（只比对最近 80 条）。
4. `shorten_sections()`：超过 90 字符的链接用 is.gd 缩短，最多 20 次请求，`SHORTEN_URLS=0` 可关。
5. `llm_review()`：一次 `/chat/completions` 调用，返回 `{briefing, highlights, sections}`。
6. 结果过少或调用失败 → 打日志并回退规则版，不中断推送。
7. `render_plain()` / `render_markdown()` 渲染两种形态。
8. 写了 key 的通道逐个推送；只有**全部通道都失败**才返回退出码 1。
9. 非搜索模式下，`total >= 3` 时写 `latest.json`；Actions 随后把该文件提交回 `main`。

## 5. 板块与限额

顺序即渲染顺序，`LLM_LIMITS` 是模型评审后每块的上限：

| key | 标题 | 来源 | 上限 |
| --- | --- | --- | --- |
| `recommend` | 🎯 你的推荐 | Google News 搜索（读者 A 画像） | 6 |
| `recommend_partner` | ✨ TA 的推荐 | Google News 搜索（读者 B 画像） | 8 |
| `search` | 🔎 搜索：关键词 | 主动搜索时替换以上全部 | 10 |
| `world` | 🌍 海外要闻 | Google News RSS (WORLD) | 4 |
| `weibo` | 🔥 微博热搜 | weibo 接口 → tophub → vvhan | 6 |
| `x` | 🐦 X 全球趋势 | trends24.in → getdaytrends | 6 |
| `github` | 💻 GitHub Trending | github.com/trending（daily） | 5 |
| `meta` | 📣 Facebook / Meta 动态 | about.fb.com 官方 RSS | 3 |
| `cn` | 🇨🇳 中文热榜 | 知乎 → tophub → 百度 | 5 |
| `wechat` | 💬 微信热文 | 自备 `WECHAT_RSS_URL` | 3 |

两栏推荐**必须保持独立**：`recommend` 服务软件/影像工程师读者，`recommend_partner`
服务影视后期、医疗、时尚、医保社保、旅游读者。禁止合并，禁止让技术内容挤占第二栏。

## 6. `latest.json` 数据契约

```json
{
  "schema": 1,
  "kind": "daily",
  "date": "2026-10-26",
  "generated_at": "2026-10-26T00:09:58+08:00",
  "title": "每日简报 10-26",
  "heading": "每日简报",
  "briefing": "120-220 字中文总览（上限 400 字）",
  "highlights": ["30 字以内", "..."],
  "count": 43,
  "sections": [
    {
      "key": "recommend",
      "icon": "🎯",
      "title": "你的推荐",
      "items": [
        {"title": "...", "url": "https://...", "note": "20 字以内", "source": "Google News"}
      ]
    }
  ],
  "skipped": ["微博热搜"]
}
```

约束：

- 空板块不会写进 `sections`，`count` 与条目总数一致。
- `briefing`、`highlights`、`note`、`source` 都可能为空或缺失，消费方必须容错。
- `skipped` 记录本次抓取失败的板块名。
- **不要手工编辑 `latest.json`**。它是生成物，改它不会改变下次输出，还会让桌面端误报。
- `date` + `generated_at` + `title` + `count` 组成提醒去重 key；任何一项变化都算新一期。

## 7. 双画像

读者 A（`USER_PROFILE` / `INTEREST_QUERIES`）：软件工程师，自动驾驶经历，现做手持影像
设备软件；关注自动驾驶、机器人、计算机视觉、计算摄影、相机/影像算法、嵌入式与边缘
计算、C++/Python、AI 工程化。

读者 B（`PARTNER_PROFILE` / `PARTNER_INTEREST_QUERIES`）：关注电影电视剧、剪辑与后期、
多发性硬化等医疗进展、医疗纠纷、时尚、中国医保社保、旅游。

搜索词用逗号或 `|` 分隔，每侧最多 8 个；`RECOMMEND_TITLE` / `PARTNER_RECOMMEND_TITLE`
可改两栏标题。画像只影响搜索与排序，不会让模型凭空生成新闻。

## 8. 密钥与变量

| 名称 | 类型 | 用途 | 缺失时 |
| --- | --- | --- | --- |
| `BARK_KEY` | Secret | iPhone 推送 | 跳过该通道 |
| `QMSG_KEY` | Secret | QQ（Qmsg 酱） | 跳过该通道 |
| `PUSHPLUS_TOKEN` | Secret | 微信（PushPlus） | 跳过该通道 |
| `SERVERCHAN_KEY` | Secret | 微信（Server 酱） | 跳过该通道 |
| `DEEPSEEK_API_KEY` / `DS` | Secret | 模型评审 | 回退规则版 |
| `LLM_API_KEY` | Secret | 任意 OpenAI 兼容模型 | 同上 |
| `LLM_BASE_URL` | Variable | 默认 `https://api.deepseek.com` | 用默认值 |
| `LLM_MODEL` | Variable | 默认 `deepseek-chat` | 用默认值 |
| `WECHAT_RSS_URL` | Variable | 微信热文源 | 不生成 wechat 板块 |
| `USER_PROFILE` / `PARTNER_PROFILE` | Variable | 画像 | 用内置默认 |
| `INTEREST_QUERIES` / `PARTNER_INTEREST_QUERIES` | Variable | 搜索词 | 用内置默认 |
| `RECOMMEND_TITLE` / `PARTNER_RECOMMEND_TITLE` | Variable | 两栏标题 | 你的推荐 / TA 的推荐 |
| `DRY_RUN` / `SHORTEN_URLS` / `QMSG_MAX_LEN` / `FEED_OUT` | 运行时 | 调试与输出控制 | 见代码默认 |

规则：任何 key 只放 GitHub Secrets，**不得**出现在代码、日志、issue、提交信息或对话里。
GitHub 会对未设置的 Secret 注入空字符串，代码必须把空值当未配置处理（已按此实现）。

## 9. 降级与容错

| 故障 | 行为 |
| --- | --- |
| 单个板块抓取失败 | 记日志、写入 `skipped`，继续其他板块 |
| 模型超时/报错/JSON 不合法 | 整期回退规则版，照常推送 |
| 模型返回的 URL 不在白名单 | 丢弃该条并记数，不重写链接 |
| 评审后条目 < 8（搜索模式 < 3） | 判为不可用，回退规则版 |
| 短链服务失败或限流 | 保留原始长链接 |
| 全部推送通道失败 | 退出码 1，Actions 标红，便于发现 |
| 桌面端拉取失败 | 用本地缓存，托盘标题显示“离线” |

## 10. Windows 客户端行为

- 数据源：`https://raw.githubusercontent.com/HyeKaguya233/news-digest/main/latest.json`
  （可用 `NEWS_FEED_URL` 覆盖，`config.json` 里也可配置多源）。
- 轮询间隔 `poll_seconds`，默认 300 秒，最小 60 秒。
- 通知去重：`last_notified` 与 `date|generated_at|title|count` 比较，相同则静默。
- 有新一期时：写 `digest.html` → `windows-toasts` 弹窗（失败回退 `winotify`）。
- 单实例：命名互斥体；重复启动时直接打开今日简报。
- 启动参数：`--minimized`（开机自启静默）、`--once`（拉一次、弹一次、退出，用于自检）。
- 数据目录 `%APPDATA%\NewsDigest\`：`config.json`、`state.json`、`latest.json`、
  `digest.html`、`app.log`。排障先看 `app.log`。
- 托盘菜单：打开今日简报 / 立即检查更新 / 开机启动 / 打开配置目录 / 退出。

注意：`raw.githubusercontent.com` 有 CDN 缓存，新一期提交后可能需要几分钟才可见，
所以“云端已推送”和“Windows 弹窗”之间最坏相差一个轮询周期 + 缓存时间。

## 11. Skill 契约

`skills/daily-news-digest/SKILL.md`：读取 `latest.json`，输出一份中文日报，A、B 两位
读者的推荐分栏呈现，保留原文链接与来源，医疗/医闹内容使用克制、可核验的措辞。
它只做整理和排版，不新增事实，不改写链接。

安装：复制 `skills/daily-news-digest` 到 `%USERPROFILE%\.codex\skills\`，新会话说
“用 daily-news-digest 整理今天的日报”。

## 12. 运行与验证

```bash
# 只打印不推送
DRY_RUN=1 FEED_OUT=latest.json python fetch_news.py

# 手动搜索
SEARCH_QUERY="计算摄影" python fetch_news.py

# 桌面端自检
NewsDigest.exe --once
```

验收清单：

1. 退出码 0；日志出现 `[推送成功]`。
2. `latest.json` 的 `count` > 0，`briefing` 句子完整，所有 `url` 以 `http` 开头。
3. `recommend` 与 `recommend_partner` 同时存在且内容不同。
4. 桌面端 `--once` 输出 `toast=True`。

## 13. 常见改动

| 需求 | 改哪里 |
| --- | --- |
| 改推送时间 | `.github/workflows/daily.yml` 的 cron（北京时间 = UTC + 8） |
| 加板块 | `gather_sections()` 的 `jobs` + 一个 `fetch_*` 函数 + `LLM_LIMITS` 加 key |
| 换模型 | 改 `LLM_BASE_URL` / `LLM_MODEL` 变量，或换 `LLM_API_KEY` |
| 加推送通道 | 参照 `push_bark()` 写函数，在 `main()` 的 `channels` 里注册 |
| 调推荐口味 | 改 `INTEREST_QUERIES` / `PARTNER_INTEREST_QUERIES` 或画像变量 |
| 改轮询频率 | 桌面端 `config.json` 的 `poll_seconds`（≥60） |

## 14. 已知限制

- 微信公众号没有稳定公开热榜，`wechat` 板块依赖自建或授权 RSS。
- Facebook 已取消公开 Trending，`meta` 板块用 Meta 官方新闻替代。
- Qmsg 免费版有内容审核，时政类消息可能被拦截；这是通道限制，不是脚本问题。
- 热榜接口均为非官方，随时可能改版；已做多源回退，但仍需关注 `skipped`。
- 单次模型调用约为几千 token 量级（输入候选 + 输出 JSON），成本随板块条数线性增长。

## 15. 给 AI 协作者

1. 先读 `AGENTS.md` 的“不可破坏的约定”，再动代码。
2. 修改后至少跑一次 `DRY_RUN=1`，确认板块数量与链接正常。
3. 涉及 `latest.json` 结构的改动，必须同时更新桌面端与 Skill 的兼容逻辑。
4. 不要把密钥写进仓库；不要在日志里打印完整 key。
5. 不要为了让测试通过而删除降级分支——那些分支是线上稳定性的来源。
