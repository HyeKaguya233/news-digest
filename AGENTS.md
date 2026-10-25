# AGENTS.md

本仓库是每日新闻简报服务。维护时优先保证：推送不中断、两栏推荐彼此独立、每条内容
可追溯、密钥不进入版本库。

## 项目结构

- `fetch_news.py`：抓取、规则清洗、双画像、AI 评审、通道推送、`latest.json` 输出。
- `.github/workflows/daily.yml`：每天 UTC 02:30（北京时间 10:30）运行，并在成功后提交
  `latest.json`。
- `skills/daily-news-digest/SKILL.md`：把 `latest.json` 整理成中文双人日报。
- `desktop/`：Windows 托盘客户端源码、构建脚本和已构建 EXE。
- `README.md`：部署、配置、运行、构建和排障的权威说明。

## 不可破坏的约定

1. 保留两个独立推荐板块：`recommend`（第一位读者）和 `recommend_partner`（第二位读者）。
   不要把两栏合并，也不要让第一栏的技术内容挤占第二栏。
2. 所有新闻条目必须保留原始 `url`。模型评审只能从输入 URL 白名单中选择；URL 不匹配
   时必须丢弃该条，不能改写链接。
3. `latest.json` 是 Actions 生成物。不要在仓库里手工改它；需要更新时运行工作流。
4. 公众号、知乎、微博等数据源没有稳定官方接口，必须保持单源失败不影响其他板块。
5. Windows 客户端和 Skill 必须兼容旧版 `latest.json`：`briefing` 和 `source` 可能缺失。
6. 医疗内容不得写成诊断或治疗建议；医闹内容使用中性、注明来源的措辞。
7. 不提交 API key、token、cookie、`.env`、日志或个人配置。密钥只放 GitHub Secrets。

## 双画像

第一位读者：软件工程师，自动驾驶经历，目前做手持影像设备软件；关注自动驾驶、机器人、
计算机视觉、计算摄影、相机/影像算法、嵌入式与边缘计算、C++/Python、AI 工程化。

第二位读者：关注电影电视剧、剪辑与后期、多发性硬化等医疗进展、医疗纠纷、时尚、
中国医保社保、旅游。偏好可靠来源、明确进展和可执行信息，不要泛泛的生活方式内容。

配置入口是 GitHub 仓库 Variables：`USER_PROFILE`、`PARTNER_PROFILE`、
`INTEREST_QUERIES`、`PARTNER_INTEREST_QUERIES`、`RECOMMEND_TITLE`、
`PARTNER_RECOMMEND_TITLE`。搜索词用逗号或 `|` 分隔，每次最多 8 个。

## 运行和验证

```powershell
python -m pip install -r requirements.txt
$env:DRY_RUN='1'
python fetch_news.py
```

修改 `fetch_news.py` 后至少检查：

- `python -m py_compile fetch_news.py`
- 干跑退出码为 0，正文包含 `你的推荐`、`TA 的推荐`、`海外要闻`。
- 模拟或检查 `write_feed`，确认顶层有 `briefing`，item 有 `source`，两个推荐 key 都存在。
- 不把真实推送当作自动测试；需要真实推送时由用户明确确认。

修改 Skill 后运行 `$skill-creator` 的校验流程或等价的前置元数据/结构校验。

修改 Windows 客户端后：

```powershell
cd desktop
.\build.ps1
python news_tray.py --once
```

自动测试使用临时 `APPDATA`，不要读取或修改真实用户的 `%APPDATA%\NewsDigest\`。

## 发布

- 先更新代码和 README，再提交，最后手动触发一次 `daily-news-digest` 工作流。
- 检查 Actions 成功后，`latest.json` 应由 Bot 自动提交回仓库。
- 检查 Raw 地址可读，并确认 `recommend_partner` 的内容覆盖多个目标主题。
- 不在 Issue、提交信息、聊天回复中粘贴任何真实密钥。

