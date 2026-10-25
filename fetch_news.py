#!/usr/bin/env python3
"""每日新闻摘要：抓取公开热榜，推送到 iPhone / QQ / 微信。

每个条目都带原始链接。

推送通道（配置了哪个就发哪个，可同时配多个）：
  BARK_KEY        Bark     -> iPhone 通知（key 或完整 api.day.app 地址）
  QMSG_KEY        Qmsg酱   -> QQ（免费版有内容审核，时政新闻会被拦）
  PUSHPLUS_TOKEN  PushPlus -> 微信
  SERVERCHAN_KEY  Server酱 -> 微信

可选配置：
  WECHAT_RSS_URL  你自己的微信 RSS 源（WeRSS / RSSHub 等）
  QMSG_MAX_LEN    QQ 单条消息长度上限，默认 1700 字（Qmsg 硬限制 1800）
  DEEPSEEK_API_KEY 配置后启用模型评审（或 LLM_API_KEY + LLM_BASE_URL + LLM_MODEL）
  USER_PROFILE    第一位读者的职业与兴趣画像，用于「你的推荐」排序
  INTEREST_QUERIES 第一位读者的个性化搜索词，逗号或竖线分隔
  PARTNER_PROFILE 第二位读者的兴趣画像，用于「TA 的推荐」排序
  PARTNER_INTEREST_QUERIES 第二位读者的个性化搜索词，逗号或竖线分隔
  SEARCH_QUERY    手动搜索关键词；配置后只推送该关键词的搜索结果
  DRY_RUN=1       只在终端打印，不推送
"""

from __future__ import annotations

import datetime as dt
import difflib
import html
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None

try:
    import feedparser
except ImportError:  # pragma: no cover
    feedparser = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover
            pass


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    if Retry is not None:
        retry = Retry(
            total=2,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    return session


SESSION = make_session()
_TOPHUB_LOCK = threading.Lock()


DEFAULT_PROFILE = (
    "软件工程师，有自动驾驶从业背景，目前从事手持影像设备软件开发。"
    "重点关注自动驾驶、机器人、计算机视觉、计算摄影、相机与影像算法、"
    "嵌入式与边缘计算、C++/Python、AI 工程化、DJI 及消费影像产品。"
)

DEFAULT_INTEREST_QUERIES = (
    "autonomous driving",
    "computer vision",
    "computational photography",
    "camera imaging software",
    "robotics engineering",
    "DJI",
)

DEFAULT_PARTNER_PROFILE = (
    "第二位读者，关注影视工业、电影与电视剧资讯、视频剪辑与后期制作、"
    "多发性硬化等神经系统疾病、医疗行业与医疗纠纷、时尚品牌与消费趋势、"
    "中国医保社保政策，以及旅游目的地和出行实用信息。"
    "偏好可靠来源、明确进展、政策变化和可执行信息，不需要营销软文或泛泛的生活方式内容。"
)

DEFAULT_PARTNER_INTEREST_QUERIES = (
    "电影 电视剧 影视资讯",
    "视频剪辑 后期制作 DaVinci Resolve",
    "多发性硬化 研究 治疗",
    "医疗纠纷 医院",
    "时尚 品牌 趋势",
    "医保 社保 政策",
    "旅游 签证 目的地",
    "电影 票房 新片",
)


def personal_profile() -> str:
    return os.environ.get("USER_PROFILE", "").strip() or DEFAULT_PROFILE


def partner_profile() -> str:
    return os.environ.get("PARTNER_PROFILE", "").strip() or DEFAULT_PARTNER_PROFILE


def queries_from_env(name: str, default):
    raw = os.environ.get(name, "").strip()
    parts = re.split(r"[|,，]+", raw) if raw else []
    queries = [clean(part, 60) for part in parts if part.strip()]
    return tuple(queries[:8]) or default


def interest_queries():
    return queries_from_env("INTEREST_QUERIES", DEFAULT_INTEREST_QUERIES)


def partner_interest_queries():
    return queries_from_env("PARTNER_INTEREST_QUERIES", DEFAULT_PARTNER_INTEREST_QUERIES)


def recommend_title() -> str:
    return os.environ.get("RECOMMEND_TITLE", "").strip() or "你的推荐"


def partner_recommend_title() -> str:
    return os.environ.get("PARTNER_RECOMMEND_TITLE", "").strip() or "TA 的推荐"


def clean(text: str, limit: int = 80) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def mk(title: str, url: str = "", source: str = "") -> dict:
    return {
        "title": clean(title),
        "url": (url or "").strip(),
        "source": clean(source, 48),
    }


def dedupe_items(items):
    seen = set()
    result = []
    for item in items:
        key = item.get("title")
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def normalize(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text or "").lower()


# ---------------------------------------------------------------- 数据源


def tophub_top(node_id: str, limit: int):
    """今日热榜的榜单页，结构稳定，作为国内站点被反爬时的兜底。"""
    soup = None
    with _TOPHUB_LOCK:
        # 连续访问多个榜单会被判定为爬虫，串行并留出间隔，失败重试一次。
        for attempt in range(2):
            time.sleep(2.5 if attempt == 0 else 3.0)
            response = SESSION.get(f"https://tophub.today/n/{node_id}", timeout=25)
            response.raise_for_status()
            candidate = BeautifulSoup(response.content, "html.parser")
            if candidate.select("table tr"):
                soup = candidate
                break
        if soup is None:
            raise RuntimeError(f"tophub({node_id}) 被限流或页面为空")

    items = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        word = ""
        link = ""
        for cell in cells[1:]:
            text = clean(cell.get_text(" ", strip=True))
            if len(text) >= 2:
                word = text
                anchor = cell.find("a")
                link = (anchor.get("href") if anchor else "") or ""
                break
        word = re.sub(r"\s*\d+(?:\.\d+)?\s*万?\s*热度$", "", word).strip()
        if word:
            items.append(mk(word, link))
        if len(items) >= limit:
            break
    if not items:
        raise RuntimeError(f"tophub({node_id}) 解析为空")
    return items


def vvhan_top(kind: str, limit: int):
    response = SESSION.get(f"https://api.vvhan.com/api/hotlist/{kind}", timeout=25)
    response.raise_for_status()
    payload = response.json()
    items = []
    for row in payload.get("data") or []:
        if isinstance(row, dict):
            items.append(
                mk(row.get("title") or row.get("name") or "", row.get("url") or "")
            )
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError(f"vvhan({kind}) 解析为空")
    return items


def fetch_world_news(limit: int = 6):
    if feedparser is None:
        raise RuntimeError("feedparser 未安装")
    url = (
        "https://news.google.com/rss/headlines/section/topic/WORLD"
        "?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    response = SESSION.get(url, timeout=25)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    items = []
    for entry in feed.entries[: limit * 3]:
        title = clean(entry.get("title", ""))
        title = re.sub(r"\s-\s[^-]{1,24}$", "", title).strip()
        source = entry.get("source") or {}
        items.append(mk(title, entry.get("link", ""), source.get("title") or ""))
        if len(dedupe_items(items)) >= limit:
            break
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError("Google News 返回为空")
    return items


def fetch_weibo_hot(limit: int = 10):
    try:
        response = SESSION.get("https://weibo.com/ajax/side/hotSearch", timeout=15)
        response.raise_for_status()
        payload = response.json()
        rows = (payload.get("data") or {}).get("realtime") or []
        items = []
        for row in rows:
            if row.get("is_ad"):
                continue
            word = row.get("word") or row.get("note") or ""
            items.append(
                mk(word, "https://s.weibo.com/weibo?q=" + quote(f"#{word}#"))
            )
        items = dedupe_items(items)[:limit]
        if items:
            return items
        log("[微博] 官方接口返回为空，改用兜底源")
    except Exception as exc:  # noqa: BLE001
        log(f"[微博] 官方接口失败：{exc}，改用兜底源")

    errors = []
    for name, loader in (
        ("tophub", lambda: tophub_top("KqndgxeLl9", limit)),
        ("vvhan", lambda: vvhan_top("wbHot", limit)),
    ):
        try:
            return loader()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    raise RuntimeError("微博热搜全部源失败（" + "；".join(errors) + "）")


def fetch_x_trends(limit: int = 10):
    errors = []
    for url in ("https://trends24.in/united-states/", "https://trends24.in/"):
        try:
            response = SESSION.get(url, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            card = soup.select_one(".trend-card__list")
            links = card.select("a") if card else soup.select(".trend-card__list a")
            items = []
            for link in links:
                items.append(mk(link.get_text(" ", strip=True), link.get("href") or ""))
            items = dedupe_items(items)[:limit]
            if items:
                return items
            raise RuntimeError(f"{url} 未解析到条目")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")

    try:
        response = SESSION.get("https://getdaytrends.com/", timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        items = []
        for link in soup.select("#trends a[href^='/trend/']"):
            href = link.get("href") or ""
            items.append(mk(link.get_text(" ", strip=True), "https://getdaytrends.com" + href))
        items = dedupe_items(items)[:limit]
        if items:
            return items
        raise RuntimeError("getdaytrends 未解析到条目")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"getdaytrends: {exc}")

    raise RuntimeError("X 趋势抓取失败（" + "；".join(errors) + "）")


def fetch_github_trending(limit: int = 8):
    response = SESSION.get("https://github.com/trending?since=daily", timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    items = []
    for article in soup.select("article.Box-row"):
        title_link = article.select_one("h2 a")
        if title_link is None:
            continue
        repo = re.sub(r"\s+", "", title_link.get_text(" ", strip=True))
        desc_node = article.select_one("p")
        desc = clean(desc_node.get_text(" ", strip=True), 44) if desc_node else ""
        star_node = article.select_one("span.float-sm-right")
        stars = clean(star_node.get_text(" ", strip=True), 18) if star_node else ""
        line = f"{repo} — {desc}" if desc else repo
        if stars:
            line = f"{line}（{stars}）"
        items.append(mk(line, "https://github.com/" + repo))
        if len(items) >= limit:
            break
    if not items:
        raise RuntimeError("GitHub Trending 解析为空")
    return items


def fetch_meta_news(limit: int = 4):
    if feedparser is None:
        raise RuntimeError("feedparser 未安装")
    response = SESSION.get("https://about.fb.com/news/feed/", timeout=25)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    items = [
        mk(entry.get("title", ""), entry.get("link", "")) for entry in feed.entries[:limit]
    ]
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError("Meta 新闻源为空")
    return items


def fetch_zhihu_hot(limit: int = 8):
    response = SESSION.get(
        "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=20",
        timeout=25,
        headers={"Referer": "https://www.zhihu.com/hot"},
    )
    response.raise_for_status()
    payload = response.json()
    items = []
    for row in payload.get("data") or []:
        target = row.get("target") or {}
        items.append(mk(target.get("title", ""), target.get("url", "")))
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError("知乎热榜返回为空")
    return items


def collect_baidu_items(node, out, depth: int = 0):
    if depth > 20:
        return
    if isinstance(node, dict):
        word = node.get("word") or node.get("query")
        if isinstance(word, str):
            url = node.get("url") or node.get("rawUrl") or ""
            out.append(mk(word, url))
        for value in node.values():
            collect_baidu_items(value, out, depth + 1)
    elif isinstance(node, list):
        for value in node:
            collect_baidu_items(value, out, depth + 1)


def fetch_baidu_hot(limit: int = 8):
    response = SESSION.get(
        "https://top.baidu.com/api/board?platform=wise&tab=realtime", timeout=25
    )
    response.raise_for_status()
    payload = response.json()
    items = []
    collect_baidu_items(payload, items)
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError("百度热搜返回为空")
    return items


def fetch_cn_hot(limit: int = 8):
    sources = (
        ("知乎接口", fetch_zhihu_hot),
        ("tophub知乎榜", lambda limit=8: tophub_top("mproPpoq6O", limit)),
        ("百度热搜", fetch_baidu_hot),
        ("vvhan", lambda limit=8: vvhan_top("zhihuHot", limit)),
    )
    for name, fetcher in sources:
        try:
            return fetcher(limit)
        except Exception as exc:  # noqa: BLE001
            log(f"[中文热榜] {name} 失败：{exc}")
    raise RuntimeError("知乎和百度热榜都抓取失败")


def fetch_wechat_rss(url: str, limit: int = 5):
    if feedparser is None:
        raise RuntimeError("feedparser 未安装")
    response = SESSION.get(url, timeout=25)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    items = [
        mk(entry.get("title", ""), entry.get("link", "")) for entry in feed.entries[:limit]
    ]
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError("微信 RSS 为空")
    return items


def fetch_google_news_search(query: str, limit: int = 4):
    if feedparser is None:
        raise RuntimeError("feedparser 未安装")
    chinese = bool(re.search(r"[\u4e00-\u9fff]", query))
    plain = not any(token in query.upper() for token in ('"', " OR ", " AND ", "SITE:", "WHEN:"))
    term = f'"{query}"' if plain and not chinese else query
    search = term if "when:" in query.lower() else f"{term} when:7d"
    response = SESSION.get(
        "https://news.google.com/rss/search",
        params={
            "q": search,
            "hl": "zh-CN" if chinese else "en-US",
            "gl": "CN" if chinese else "US",
            "ceid": "CN:zh-Hans" if chinese else "US:en",
        },
        timeout=25,
    )
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    items = []
    for entry in feed.entries[: limit * 3]:
        title = clean(entry.get("title", ""))
        title = re.sub(r"\s-\s[^-]{1,32}$", "", title).strip()
        source = entry.get("source") or {}
        source_name = source.get("title") or ""
        source_url = source.get("href") or ""
        if is_low_quality(title, source_url):
            continue
        items.append(mk(title, entry.get("link", ""), source_name))
        if len(dedupe_items(items)) >= limit:
            break
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError(f"Google News 搜索「{query}」为空")
    return items


def fetch_hn_search(query: str, limit: int = 5):
    cutoff = int(time.time()) - 30 * 86400
    response = SESSION.get(
        "https://hn.algolia.com/api/v1/search",
        params={
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff},points>20",
            "hitsPerPage": max(limit * 3, 10),
        },
        timeout=25,
    )
    response.raise_for_status()
    items = []
    needle = query.lower().strip()
    tokens = [token for token in re.split(r"[^a-z0-9]+", needle) if len(token) >= 2]
    for hit in response.json().get("hits") or []:
        title = hit.get("title") or ""
        lowered = title.lower()
        if needle not in lowered and not any(token in lowered for token in tokens):
            continue
        object_id = hit.get("objectID") or ""
        url = hit.get("url") or (
            f"https://news.ycombinator.com/item?id={object_id}" if object_id else ""
        )
        items.append(mk(title, url))
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError(f"Hacker News 搜索「{query}」为空")
    return items


def fetch_github_search(query: str, limit: int = 5):
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    response = SESSION.get(
        "https://api.github.com/search/repositories",
        params={
            "q": f"{query} pushed:>={since} stars:>=20",
            "sort": "stars",
            "order": "desc",
            "per_page": max(limit * 2, 10),
        },
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=25,
    )
    response.raise_for_status()
    items = []
    for row in response.json().get("items") or []:
        repo = row.get("full_name") or ""
        desc = clean(row.get("description") or "", 54)
        line = f"{repo} — {desc}" if desc else repo
        items.append(mk(line, row.get("html_url") or ""))
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError(f"GitHub 搜索「{query}」为空")
    return items


def fetch_recommendations(queries, limit: int, label: str):
    groups = []
    errors = []
    for query in queries:
        try:
            groups.append(fetch_google_news_search(query, 4))
        except Exception as exc:  # noqa: BLE001
            log(f"[{label}] 搜索「{query}」失败：{exc}")
            errors.append(f"{query}: {exc}")
            groups.append([])
    items = []
    for index in range(4):
        for group in groups:
            if index < len(group):
                items.append(group[index])
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError(f"{label}全部源失败（" + "；".join(errors) + "）")
    return items


def fetch_personal_recommendations(limit: int = 10):
    return fetch_recommendations(interest_queries(), limit, "你的推荐")


def fetch_partner_recommendations(limit: int = 12):
    return fetch_recommendations(partner_interest_queries(), limit, "TA 的推荐")


def fetch_topic_search(query: str, limit: int = 20):
    items = []
    errors = []
    sources = (
        ("Google News", lambda: fetch_google_news_search(query, 12)),
        ("Hacker News", lambda: fetch_hn_search(query, 6)),
        ("GitHub", lambda: fetch_github_search(query, 5)),
    )
    for name, fetcher in sources:
        try:
            items.extend(fetcher())
        except Exception as exc:  # noqa: BLE001
            log(f"[搜索] {name} 失败：{exc}")
            errors.append(f"{name}: {exc}")
    items = dedupe_items(items)[:limit]
    if not items:
        raise RuntimeError("搜索源全部失败（" + "；".join(errors) + "）")
    return items


# ---------------------------------------------------------------- 质量处理


LOW_QUALITY_WORDS = ("抽奖", "领红包", "优惠券", "限时秒杀", "点击领取", "广告位", "返利")
LOW_QUALITY_DOMAINS = ("henan100.com", "2265.com", "downcc.com", "pc6.com")
DOWNLOAD_SPAM_WORDS = (
    "最新版",
    "安卓版",
    "iphone版",
    "下载",
    "破解版",
    "免费观看",
    "在线观看",
    "安装包",
)


def is_low_quality(title: str, source_url: str = "") -> bool:
    if len(title) < 5:
        return True
    if any(word in title for word in LOW_QUALITY_WORDS):
        return True
    if any(domain in (source_url or "").lower() for domain in LOW_QUALITY_DOMAINS):
        return True
    lowered = title.lower()
    if sum(word in lowered for word in DOWNLOAD_SPAM_WORDS) >= 2:
        return True
    if title.count("#") >= 6:
        return True
    return False


def apply_quality_rules(sections):
    """跨板块近似去重 + 广告/低质过滤，保持板块内原有排序。"""
    seen = []
    removed = 0
    for section in sections:
        kept = []
        for item in section["items"]:
            title = item["title"]
            key = normalize(title)
            if len(key) < 4 or is_low_quality(title):
                removed += 1
                continue
            if any(
                difflib.SequenceMatcher(None, key, previous).ratio() >= 0.85
                for previous in seen[-80:]
            ):
                removed += 1
                continue
            seen.append(key)
            kept.append(item)
        section["items"] = kept
    if removed:
        log(f"[质量规则] 过滤 {removed} 条重复或低质内容")
    return sections


def shorten_url(url: str, cache: dict, limit: int = 90):
    """过长的链接（尤其是 Google News 跳转）转成短链，失败就保留原链接。"""
    if not url or len(url) <= limit:
        return url
    if url in cache:
        return cache[url]
    try:
        response = SESSION.get(
            "https://is.gd/create.php",
            params={"format": "simple", "url": url},
            timeout=15,
        )
        short = response.text.strip()
        if response.ok and short.startswith("https://is.gd/"):
            cache[url] = short
            time.sleep(0.4)
            return short
    except Exception as exc:  # noqa: BLE001
        log(f"[短链] 转换失败，保留原链接：{exc}")
    cache[url] = url
    return url


def shorten_sections(sections, limit: int = 90, max_requests: int = 20):
    if os.environ.get("SHORTEN_URLS", "1").strip() in {"0", "false", "no"}:
        return sections
    cache = {}
    requests_made = 0
    for section in sections:
        for item in section["items"]:
            url = item.get("url") or ""
            if len(url) <= limit:
                continue
            if requests_made >= max_requests:
                continue
            if url not in cache:
                requests_made += 1
            item["url"] = shorten_url(url, cache, limit)
    return sections


# ---------------------------------------------------------------- 模型评审


LLM_LIMITS = {
    "recommend": 6,
    "recommend_partner": 8,
    "search": 10,
    "world": 4,
    "weibo": 6,
    "x": 6,
    "github": 5,
    "meta": 3,
    "cn": 5,
    "wechat": 3,
}

LLM_SYSTEM = (
    "你是资深中文新闻编辑，负责给两位读者做每日简报的筛选、分组和排序。"
    "你只输出 JSON，不输出任何解释文字。"
)


def extract_json(text: str):
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出中没有 JSON")
    return json.loads(text[start : end + 1])


def llm_review(sections):
    """调用模型做去重、去标题党、双画像排序和日报总览。失败时返回 None。"""
    key = (
        os.environ.get("DEEPSEEK_API_KEY", "").strip()
        or os.environ.get("LLM_API_KEY", "").strip()
    )
    if not key:
        return None
    base = (
        os.environ.get("LLM_BASE_URL", "").strip() or "https://api.deepseek.com"
    ).rstrip("/")
    model = os.environ.get("LLM_MODEL", "").strip() or "deepseek-chat"

    payload = {
        "limits": LLM_LIMITS,
        "profiles": {
            "primary": personal_profile(),
            "partner": partner_profile(),
        },
        "search_query": os.environ.get("SEARCH_QUERY", "").strip(),
        "sections": [
            {
                "key": section["key"],
                "title": section["title"],
                "items": [
                    {
                        "title": item["title"],
                        "url": item["url"],
                        "source": item.get("source") or "",
                    }
                    for item in section["items"]
                ],
            }
            for section in sections
            if section["items"]
        ],
    }
    prompt = (
        "下面是今天抓取的候选新闻，按板块分组，limits 是每个板块最多保留条数。\n\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n任务：\n"
        "1. 合并报道同一事件的多条，只保留信息最完整的一条\n"
        "2. 删除标题党、广告、软文、无信息量的内容\n"
        "3. 每个板块按重要性从高到低排序，不超过 limits 条\n"
        "4. 每条给一句 20 字以内的中文说明 note，不要复述标题\n"
        "5. 写一段 120-220 字的中文 briefing，总览今天最值得知道的 3-5 件事，同时照顾两位读者；不要编造输入中没有的事实\n"
        "6. 挑 3-5 条今天最值得知道的，写成 30 字以内的 highlights\n"
        "7. recommend 必须贴合 profiles.primary，优先技术含量高、与当前工作相关的内容\n"
        "8. recommend_partner 必须贴合 profiles.partner；覆盖影视/后期、医疗、时尚、医保社保、旅游中的多个主题，不要把第一条读者的技术内容混进去\n"
        "9. recommend_partner 的医疗内容只保留来源可靠、有明确研究或政策进展的内容，不把新闻写成诊断或治疗建议；医闹类只保留事实清楚、来源可核验且措辞克制的报道\n"
        "10. search_query 非空时，search 板块是用户主动搜索，优先直接相关且信息量高的结果\n\n"
        "约束：url 必须原样复制输入中的链接，不得编造或修改；保留原文语言；参考 source 判断来源可靠性；只输出 JSON。\n\n"
        '输出格式：{"briefing":"...","highlights":["..."],"sections":[{"key":"world","items":[{"title":"...","url":"...","note":"..."}]}]}'
    )

    try:
        response = SESSION.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": LLM_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=120,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        data = extract_json(content)
    except Exception as exc:  # noqa: BLE001
        log(f"[模型评审] 调用失败，回退到规则版：{exc}")
        return None

    by_key = {section["key"]: section for section in sections}
    reviewed = []
    dropped = 0
    for section in data.get("sections") or []:
        target = by_key.get(section.get("key"))
        if target is None:
            continue
        allowed = {item["url"]: item for item in target["items"]}
        items = []
        for row in section.get("items") or []:
            url = (row.get("url") or "").strip()
            original = allowed.get(url)
            if original is None:
                dropped += 1
                continue
            items.append(
                {
                    "title": clean(row.get("title") or original["title"]),
                    "url": url,
                    "note": clean(row.get("note") or "", 24),
                    "source": original.get("source") or "",
                }
            )
        reviewed.append({**target, "items": items})

    kept = {section["key"] for section in reviewed}
    reviewed.extend(section for section in sections if section["key"] not in kept)

    total = sum(len(section["items"]) for section in reviewed)
    minimum = 3 if os.environ.get("SEARCH_QUERY", "").strip() else 8
    if total < minimum:
        log(f"[模型评审] 结果仅 {total} 条，回退到规则版")
        return None
    if dropped:
        log(f"[模型评审] 丢弃 {dropped} 条链接不匹配的条目")

    highlights = [clean(text, 40) for text in (data.get("highlights") or [])[:5]]
    briefing = clean(data.get("briefing") or "", 400)
    log(f"[模型评审] 完成，保留 {total} 条")
    return reviewed, highlights, briefing


def gather_sections():
    search_query = os.environ.get("SEARCH_QUERY", "").strip()
    if search_query:
        errors = []
        try:
            items = fetch_topic_search(search_query)
        except Exception as exc:  # noqa: BLE001
            log(f"[搜索] 失败：{exc}")
            items = []
            errors.append("搜索")
        sections = [
            {
                "key": "search",
                "icon": "🔎",
                "title": f"搜索：{clean(search_query, 24)}",
                "items": items,
            }
        ]
        return apply_quality_rules(sections), errors

    jobs = [
        ("recommend", "🎯", recommend_title(), fetch_personal_recommendations),
        (
            "recommend_partner",
            "✨",
            partner_recommend_title(),
            fetch_partner_recommendations,
        ),
        ("world", "🌍", "海外要闻", fetch_world_news),
        ("weibo", "🔥", "微博热搜", fetch_weibo_hot),
        ("x", "🐦", "X 全球趋势", fetch_x_trends),
        ("github", "💻", "GitHub Trending", fetch_github_trending),
        ("meta", "📣", "Facebook / Meta 动态", fetch_meta_news),
        ("cn", "🇨🇳", "中文热榜", fetch_cn_hot),
    ]

    wechat_rss = os.environ.get("WECHAT_RSS_URL", "").strip()
    if wechat_rss:
        jobs.append(
            (
                "wechat",
                "💬",
                "微信热文",
                lambda limit=5: fetch_wechat_rss(wechat_rss, limit),
            )
        )

    sections = []
    errors = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {
            pool.submit(fetcher): (key, icon, title)
            for key, icon, title, fetcher in jobs
        }
        results = {}
        for future in as_completed(futures):
            key, icon, title = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:  # noqa: BLE001
                log(f"[{title}] 抓取失败：{exc}")
                errors.append(title)
                results[key] = []
        for key, icon, title, _ in jobs:
            sections.append(
                {"key": key, "icon": icon, "title": title, "items": results.get(key, [])}
            )
    return apply_quality_rules(sections), errors


# ---------------------------------------------------------------- 排版


def fit(lines, limit: int) -> str:
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    kept = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > limit - 30:
            break
        kept.append(line)
        total += len(line) + 1
    kept.append("…（内容过长，已截断）")
    return "\n".join(kept).strip()


def render_plain(
    sections,
    stamp: str,
    limit: int,
    highlights=None,
    heading: str = "每日简报",
    briefing: str = "",
) -> str:
    lines = [f"📰 {heading} · {stamp}", ""]
    if briefing:
        lines.extend(["🧭 AI 日报总览", briefing, ""])
    if highlights:
        lines.append("⭐ 今日要点")
        for text in highlights:
            lines.append(f"· {text}")
        lines.append("")
    for section in sections:
        if not section["items"]:
            continue
        lines.append(f"{section['icon']} {section['title']}")
        for index, item in enumerate(section["items"], 1):
            note = item.get("note") or ""
            lines.append(
                f"{index}. {item['title']}" + (f"｜{note}" if note else "")
            )
            if item["url"]:
                lines.append(f"   {item['url']}")
        lines.append("")
    return fit(lines, limit)


def render_markdown(
    sections,
    stamp: str,
    highlights=None,
    heading: str = "每日简报",
    briefing: str = "",
) -> str:
    lines = [f"## 📰 {heading} · {stamp}", ""]
    if briefing:
        lines.extend(["### 🧭 AI 日报总览", briefing, ""])
    if highlights:
        lines.append("### ⭐ 今日要点")
        for text in highlights:
            lines.append(f"- {text}")
        lines.append("")
    for section in sections:
        if not section["items"]:
            continue
        lines.append(f"### {section['icon']} {section['title']}")
        for index, item in enumerate(section["items"], 1):
            note = item.get("note") or ""
            suffix = f" — {note}" if note else ""
            if item["url"]:
                lines.append(f"{index}. [{item['title']}]({item['url']}){suffix}")
            else:
                lines.append(f"{index}. {item['title']}{suffix}")
        lines.append("")
    return "\n".join(lines).strip()


def write_feed(
    path: str, sections, now, title: str, highlights, errors, briefing: str = ""
):
    """写出给桌面客户端使用的结构化日报。"""
    items = [
        {
            "title": item["title"],
            "url": item["url"],
            "note": item.get("note") or "",
            "source": item.get("source") or "",
        }
        for section in sections
        for item in section["items"]
    ]
    payload = {
        "schema": 1,
        "kind": "daily",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(timespec="seconds"),
        "title": title,
        "heading": "每日简报",
        "briefing": briefing,
        "highlights": list(highlights or []),
        "count": len(items),
        "sections": [
            {
                "key": section["key"],
                "icon": section["icon"],
                "title": section["title"],
                "items": [
                    {
                        "title": item["title"],
                        "url": item["url"],
                        "note": item.get("note") or "",
                        "source": item.get("source") or "",
                    }
                    for item in section["items"]
                ],
            }
            for section in sections
            if section["items"]
        ],
        "skipped": list(errors or []),
    }
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    log(f"[Feed] 已写入 {target}（{len(items)} 条）")


# ---------------------------------------------------------------- 推送


def split_message(text: str, limit: int, max_parts: int = 3):
    """按行切分，保证每段不超过 limit 字；超出 max_parts 时截断最后一段。"""
    if len(text) <= limit:
        return [text]
    parts = []
    current = []
    total = 0
    for line in text.split("\n"):
        if total + len(line) + 1 > limit and current:
            parts.append("\n".join(current))
            current, total = [], 0
        current.append(line)
        total += len(line) + 1
    if current:
        parts.append("\n".join(current))
    if len(parts) > max_parts:
        parts = parts[:max_parts]
        parts[-1] = parts[-1][: limit - 16] + "\n…（内容过长已截断）"
    return parts


def split_by_bytes(text: str, max_bytes: int = 3400, max_parts: int = 4):
    """Bark 单次请求体约 4KB，按字节切分，避免 413。"""
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    parts = []
    current = []
    size = 0
    for line in text.split("\n"):
        line_bytes = len(line.encode("utf-8")) + 1
        if size + line_bytes > max_bytes and current:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_bytes
    if current:
        parts.append("\n".join(current))
    if len(parts) > max_parts:
        parts = parts[:max_parts]
        parts[-1] += "\n…（内容过长已截断）"
    return parts


def push_bark(key: str, title: str, text: str) -> str:
    base = "https://api.day.app"
    if key.startswith("http://") or key.startswith("https://"):
        trimmed = key.rstrip("/")
        base, key = trimmed.rsplit("/", 1)
    parts = split_by_bytes(text)
    for index, part in enumerate(parts, 1):
        if len(parts) > 1:
            part = f"（{index}/{len(parts)}）\n{part}"
        if index > 1:
            time.sleep(1.0)
        response = SESSION.post(
            f"{base}/{key}",
            json={
                "title": title,
                "body": part,
                "group": "daily-news",
                "level": "active",
                "isArchive": 1,
            },
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("code") or 0) != 200:
            raise RuntimeError(f"Bark 返回异常：{payload}")
    return f"{len(parts)} 条" if len(parts) > 1 else "ok"


def sanitize_for_qmsg(text: str) -> str:
    """Qmsg 会拦截「连续数字串」，把 4 位以上的数字转成全角，避免误判。"""
    def to_fullwidth(match):
        return "".join(chr(ord(ch) - ord("0") + ord("０")) for ch in match.group(0))

    return re.sub(r"\d{4,}", to_fullwidth, text)


def qmsg_send(key: str, text: str):
    response = SESSION.post(
        f"https://qmsg.zendee.cn/v3/send/{key}", data={"msg": text}, timeout=25
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(f"Qmsg 返回非 JSON：{response.text[:120]}")
    if not payload.get("success"):
        raise RuntimeError(f"Qmsg 返回异常：{payload.get('message') or payload}")
    return payload.get("data")


def qmsg_status(key: str, msg_id) -> int:
    try:
        response = SESSION.get(
            f"https://qmsg.zendee.cn/v3/msg/status/{key}",
            params={"msgId": msg_id},
            timeout=20,
        )
        response.raise_for_status()
        return int(response.json().get("data") or 0)
    except Exception:  # noqa: BLE001
        return 0


def push_qmsg(key: str, text: str, limit: int = 1700) -> str:
    parts = [sanitize_for_qmsg(part) for part in split_message(text, limit)]
    last_id = None
    for index, part in enumerate(parts, 1):
        if index > 1:
            part = f"（续 {index}/{len(parts)}）\n{part}"
            time.sleep(5.5)  # 同一 key 每 5 秒限一次
        last_id = qmsg_send(key, part)

    status = 0
    for _ in range(3):
        time.sleep(2)
        status = qmsg_status(key, last_id)
        if status != 0:
            break
    labels = {0: "已提交（待回执）", 1: "发送成功", 2: "内容违规", -1: "发送失败"}
    if status == 2:
        raise RuntimeError("Qmsg 判定内容违规，消息已被拦截")
    if status == -1:
        raise RuntimeError("Qmsg 发送失败（-1）")
    return f"{len(parts)} 条，msgId={last_id}，{labels.get(status, status)}"


def push_pushplus(token: str, title: str, markdown: str) -> str:
    response = SESSION.post(
        "https://www.pushplus.plus/send",
        json={
            "token": token,
            "title": title,
            "content": markdown,
            "template": "markdown",
        },
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise RuntimeError(f"PushPlus 返回异常：{payload}")
    return str(payload.get("msg", "ok"))


def push_serverchan(key: str, title: str, markdown: str) -> str:
    response = SESSION.post(
        f"https://sctapi.ftqq.com/{key}.send",
        data={"title": title, "desp": markdown},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in (0, None):
        raise RuntimeError(f"Server酱返回异常：{payload}")
    return "ok"


# ---------------------------------------------------------------- 主流程


def main() -> int:
    if feedparser is None:
        log("提示：feedparser 未安装，RSS 类数据源会被跳过")

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    stamp = now.strftime("%Y-%m-%d %H:%M")
    search_query = os.environ.get("SEARCH_QUERY", "").strip()
    heading = "搜索简报" if search_query else "每日简报"
    title = (
        f"搜索 {clean(search_query, 18)}"
        if search_query
        else now.strftime("每日简报 %m-%d")
    )

    sections, errors = gather_sections()
    sections = shorten_sections(sections)
    highlights = []
    briefing = ""
    reviewed = llm_review(sections)
    if reviewed:
        sections, highlights, briefing = reviewed
    total = sum(len(section["items"]) for section in sections)
    max_len = int(os.environ.get("QMSG_MAX_LEN", "1700"))

    feed_out = os.environ.get("FEED_OUT", "").strip()
    if feed_out and not search_query and total >= 3:
        try:
            write_feed(feed_out, sections, now, title, highlights, errors, briefing)
        except Exception as exc:  # noqa: BLE001
            log(f"[Feed] 写入失败：{exc}")

    if total < 3:
        if search_query:
            plain = f"⚠️ 搜索「{clean(search_query, 30)}」没有获得有效结果，请换个关键词再试。"
        else:
            plain = (
                f"⚠️ 每日简报 {now:%m-%d} 抓取异常，仅获得 {total} 条内容。\n"
                "可能是数据源改版或被限流，请到 Actions 日志查看。"
            )
        markdown = plain
    else:
        plain = render_plain(
            sections, stamp, 10 ** 6, highlights, heading=heading, briefing=briefing
        )
        markdown = render_markdown(
            sections, stamp, highlights, heading=heading, briefing=briefing
        )
        if errors:
            plain += f"\n\n（本次跳过：{'、'.join(errors)}）"
            markdown += f"\n\n> 本次跳过：{'、'.join(errors)}"

    if os.environ.get("DRY_RUN", "").strip() in {"1", "true", "yes"}:
        print(plain)
        log(f"共 {total} 条，正文 {len(plain)} 字")
        return 0

    channels = []
    bark_key = os.environ.get("BARK_KEY", "").strip()
    qmsg_key = os.environ.get("QMSG_KEY", "").strip()
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    serverchan_key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if bark_key:
        channels.append(("Bark(iPhone)", lambda: push_bark(bark_key, title, plain)))
    if qmsg_key:
        channels.append(("Qmsg酱(QQ)", lambda: push_qmsg(qmsg_key, plain, max_len)))
    if pushplus_token:
        channels.append(
            ("PushPlus(微信)", lambda: push_pushplus(pushplus_token, title, markdown))
        )
    if serverchan_key:
        channels.append(
            ("Server酱(微信)", lambda: push_serverchan(serverchan_key, title, markdown))
        )

    if not channels:
        log("未配置任何推送通道，以下为简报内容：")
        print(plain)
        return 0

    failed = 0
    for name, func in channels:
        try:
            detail = func()
            log(f"[推送成功] {name}: {detail}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log(f"[推送失败] {name}: {exc}")

    return 1 if failed == len(channels) else 0


if __name__ == "__main__":
    sys.exit(main())
