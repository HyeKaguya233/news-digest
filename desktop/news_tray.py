from __future__ import annotations

import ctypes
import html
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
import winreg
from datetime import datetime
from pathlib import Path
from typing import Any

import pystray
from PIL import Image, ImageDraw
from windows_toasts import Toast, WindowsToaster


APP_NAME = "NewsDigest"
APP_TITLE = "每日简报"
APP_ID = "News Digest"
MUTEX_NAME = r"Local\NewsDigestWindowsTray"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "NewsDigestWindows"

DEFAULT_FEED_URLS = [
    "https://raw.githubusercontent.com/HyeKaguya233/news-digest/main/latest.json",
    "https://cdn.jsdelivr.net/gh/HyeKaguya233/news-digest@main/latest.json",
]


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


APP_DIR = app_dir()
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state.json"
CACHE_PATH = APP_DIR / "latest.json"
HTML_PATH = APP_DIR / "digest.html"
LOG_PATH = APP_DIR / "app.log"

STOP = threading.Event()
CHECK_NOW = threading.Event()
FEED_LOCK = threading.Lock()
CURRENT_FEED: dict[str, Any] | None = None
_MUTEX_HANDLE = None


def log(message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n"
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            LOG_PATH.unlink()
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
    if not getattr(sys, "frozen", False):
        print(line.rstrip())


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def default_config() -> dict[str, Any]:
    return {
        "feed_urls": list(DEFAULT_FEED_URLS),
        "poll_seconds": 300,
        "notify_only_daily": True,
        "start_with_windows": True,
    }


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        config = default_config()
        save_json(CONFIG_PATH, config)
    else:
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log(f"config read failed: {exc}")
            config = default_config()

    urls = config.get("feed_urls") or DEFAULT_FEED_URLS
    if isinstance(urls, str):
        urls = [part.strip() for part in urls.split(",") if part.strip()]
    override = os.environ.get("NEWS_FEED_URL", "").strip()
    if override:
        urls = [override]
    config["feed_urls"] = [str(url).strip() for url in urls if str(url).strip()]
    if not config["feed_urls"]:
        config["feed_urls"] = list(DEFAULT_FEED_URLS)
    try:
        config["poll_seconds"] = max(60, int(config.get("poll_seconds", 300)))
    except (TypeError, ValueError):
        config["poll_seconds"] = 300
    return config


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_cached_feed() -> dict[str, Any] | None:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def normalize_url(value: str) -> str:
    value = value.strip()
    if value.startswith(("http://", "https://", "file://")):
        return value
    path = Path(value).expanduser()
    if path.exists():
        return path.resolve().as_uri()
    return value


def fetch_feed(config: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    errors: list[str] = []
    for raw_url in config["feed_urls"]:
        url = normalize_url(raw_url)
        try:
            request_url = url
            if url.startswith("file://"):
                file_path = Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path))
                raw = file_path.read_bytes()
            else:
                bucket = int(time.time() // 300)
                separator = "&" if "?" in url else "?"
                request_url = f"{url}{separator}_={bucket}"
                request = urllib.request.Request(
                    request_url,
                    headers={
                        "User-Agent": "NewsDigestWindows/1.0",
                        "Cache-Control": "no-cache",
                    },
                )
                with urllib.request.urlopen(request, timeout=25) as response:
                    raw = response.read()
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
                raise ValueError("feed 缺少 sections")
            if not data.get("sections"):
                raise ValueError("feed 为空")
            save_json(CACHE_PATH, data)
            return data, raw_url
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{raw_url}: {exc}")
            log(f"feed failed: {raw_url}: {exc}")
    if errors:
        log("all feed sources failed")
    return None, None


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def item_count(feed: dict[str, Any]) -> int:
    total = 0
    for section in feed.get("sections") or []:
        total += len(section.get("items") or [])
    return total


def feed_key(feed: dict[str, Any]) -> str:
    return "|".join(
        str(feed.get(key) or "")
        for key in ("date", "generated_at", "title", "count")
    )


def is_daily(feed: dict[str, Any]) -> bool:
    return (feed.get("kind") or "daily") == "daily"


def write_html(feed: dict[str, Any]) -> Path:
    title = esc(feed.get("title") or APP_TITLE)
    date = esc(feed.get("date") or "")
    generated = esc(feed.get("generated_at") or "")
    highlights = [str(text) for text in (feed.get("highlights") or []) if str(text).strip()]
    briefing = str(feed.get("briefing") or "").strip()
    total = item_count(feed)

    briefing_html = ""
    if briefing:
        briefing_html = (
            f'<section class="briefing"><h2>AI 日报总览</h2>'
            f'<p>{esc(briefing)}</p></section>'
        )

    highlight_html = ""
    if highlights:
        cards = "".join(f"<li>{esc(text)}</li>" for text in highlights)
        highlight_html = f'<section class="highlights"><h2>今日要点</h2><ul>{cards}</ul></section>'

    section_html: list[str] = []
    for section in feed.get("sections") or []:
        items = section.get("items") or []
        if not items:
            continue
        rows = []
        for item in items:
            item_title = esc(item.get("title") or "")
            note = esc(item.get("note") or "")
            source = esc(item.get("source") or "")
            url = str(item.get("url") or "").strip()
            if url:
                link = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{item_title}</a>'
            else:
                link = f"<span>{item_title}</span>"
            meta = " · ".join(
                part for part in (note, f"来源：{source}" if source else "") if part
            )
            note_html = f'<div class="note">{meta}</div>' if meta else ""
            rows.append(f"<li>{link}{note_html}</li>")
        section_html.append(
            '<section class="block">'
            f'<h2><span class="icon">{esc(section.get("icon") or "")}</span>'
            f'{esc(section.get("title") or "")}</h2>'
            f'<ul class="items">{"".join(rows)}</ul>'
            "</section>"
        )

    skipped = [str(text) for text in (feed.get("skipped") or []) if str(text).strip()]
    skipped_html = ""
    if skipped:
        skipped_html = f'<p class="skip">本次跳过：{esc("、".join(skipped))}</p>'

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --text:#17202a; --muted:#65707d; --line:#e4e8ee; --accent:#1f6feb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f141a; --card:#171e26; --text:#e8eef5; --muted:#9aa7b4; --line:#2a3542; --accent:#69a7ff; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; line-height:1.65; }}
main {{ max-width:860px; margin:0 auto; padding:34px 20px 64px; }}
header {{ margin-bottom:24px; }}
h1 {{ font-size:30px; line-height:1.25; margin:0 0 8px; }}
.meta {{ color:var(--muted); font-size:13px; }}
.briefing, .highlights, .block {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 22px; margin:16px 0; }}
.briefing {{ border-left:4px solid var(--accent); }}
.briefing h2, .highlights h2, .block h2 {{ font-size:18px; margin:0 0 12px; }}
.briefing p {{ margin:0; }}
.highlights ul {{ margin:0; padding-left:20px; }}
.highlights li {{ margin:6px 0; }}
.items {{ list-style:none; margin:0; padding:0; }}
.items li {{ padding:12px 0; border-top:1px solid var(--line); }}
.items li:first-child {{ border-top:0; padding-top:0; }}
.items a {{ color:var(--accent); text-decoration:none; font-weight:600; }}
.items a:hover {{ text-decoration:underline; }}
.note {{ color:var(--muted); font-size:13px; margin-top:3px; }}
.icon {{ margin-right:6px; }}
.skip {{ color:var(--muted); font-size:12px; }}
footer {{ color:var(--muted); font-size:12px; margin-top:28px; text-align:center; }}
</style>
</head>
<body>
<main>
<header>
<h1>{title}</h1>
<div class="meta">{date} · 共 {total} 条 · 生成于 {generated}</div>
</header>
{briefing_html}
{highlight_html}
{''.join(section_html)}
{skipped_html}
<footer>由 GitHub Actions 生成 · Windows 客户端本地渲染</footer>
</main>
</body>
</html>
"""
    tmp = HTML_PATH.with_name(HTML_PATH.name + ".tmp")
    tmp.write_text(document, encoding="utf-8")
    os.replace(tmp, HTML_PATH)
    return HTML_PATH


def summary_text(feed: dict[str, Any]) -> str:
    lines: list[str] = []
    for text in (feed.get("highlights") or [])[:2]:
        text = str(text).strip()
        if text:
            lines.append("· " + (text[:58] + "…" if len(text) > 58 else text))
    if not lines:
        for section in feed.get("sections") or []:
            for item in section.get("items") or []:
                title = str(item.get("title") or "").strip()
                if title:
                    lines.append("· " + (title[:58] + "…" if len(title) > 58 else title))
                    break
            if lines:
                break
    lines.append(f"共 {item_count(feed)} 条 · 点击查看完整简报")
    return "\n".join(lines)


def open_digest(feed: dict[str, Any] | None = None) -> None:
    with FEED_LOCK:
        data = feed or CURRENT_FEED
    if data is None:
        data = load_cached_feed()
    if data is None:
        show_message_toast("暂无简报", "请点击托盘菜单的“立即检查更新”。")
        return
    path = write_html(data)
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        webbrowser.open(path.as_uri())


def show_message_toast(title: str, message: str) -> bool:
    try:
        WindowsToaster(APP_ID).show_toast(
            Toast(text_fields=[title, message], on_activated=lambda _args: None)
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"windows-toasts message failed: {exc}")
    try:
        from winotify import Notification

        Notification(app_id=APP_ID, title=title, msg=message).show()
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"winotify message failed: {exc}")
        return False


def show_manual_start_feedback() -> None:
    cached = load_cached_feed()
    if cached is not None:
        open_digest(cached)
        show_message_toast(
            "每日简报已启动",
            "完整简报已打开；程序会继续在右下角托盘运行。",
        )
        return
    ctypes.windll.user32.MessageBoxW(
        0,
        "每日简报已启动，正在获取最新一期。\n\n"
        "右下角托盘会出现蓝色 N 图标（可能在 ^ 展开区）。\n"
        "有新一期时会弹出 Windows 通知。",
        "每日简报",
        0x40,
    )


def show_toast(feed: dict[str, Any]) -> bool:
    title = str(feed.get("title") or APP_TITLE)
    message = summary_text(feed)
    path = write_html(feed)
    try:
        toast = Toast(
            text_fields=[title, message],
            on_activated=lambda _args: open_digest(feed),
        )
        WindowsToaster(APP_ID).show_toast(toast)
        log("toast shown via windows-toasts")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"windows-toasts failed: {exc}")

    try:
        from winotify import Notification

        Notification(
            app_id=APP_ID,
            title=title,
            msg=message,
            launch=path.as_uri(),
        ).show()
        log("toast shown via winotify")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"winotify failed: {exc}")
        return False


def maybe_notify(feed: dict[str, Any], config: dict[str, Any]) -> None:
    if config.get("notify_only_daily", True) and not is_daily(feed):
        return
    key = feed_key(feed)
    state = load_state()
    if state.get("last_notified") == key:
        return
    if show_toast(feed):
        state["last_notified"] = key
        state["last_notified_at"] = datetime.now().isoformat(timespec="seconds")
        save_json(STATE_PATH, state)


def poll_loop(icon: pystray.Icon) -> None:
    global CURRENT_FEED
    while not STOP.is_set():
        config = load_config()
        feed, source = fetch_feed(config)
        if feed is not None:
            with FEED_LOCK:
                CURRENT_FEED = feed
            maybe_notify(feed, config)
            icon.title = f"{APP_TITLE} · {feed.get('date') or ''}".strip()
            log(f"feed loaded from {source}")
        else:
            cached = load_cached_feed()
            if cached is not None:
                with FEED_LOCK:
                    CURRENT_FEED = cached
            icon.title = f"{APP_TITLE} · 离线"
        CHECK_NOW.wait(config["poll_seconds"])
        CHECK_NOW.clear()


def startup_command() -> str:
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{exe}" --minimized'
    return f'"{exe}" "{Path(__file__).resolve()}" --minimized'


def set_startup(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        log(f"startup update failed: {exc}")
        return False


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def toggle_startup(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    set_startup(not startup_enabled())


def open_config_folder(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    try:
        os.startfile(str(APP_DIR))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        webbrowser.open(APP_DIR.as_uri())


def quit_app(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    STOP.set()
    CHECK_NOW.set()
    icon.stop()


def load_icon() -> Image.Image:
    icon_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "news.ico"
    try:
        return Image.open(icon_path).convert("RGBA")
    except Exception:  # noqa: BLE001
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(31, 111, 235, 255))
        draw.line((19, 46, 19, 18), fill="white", width=6)
        draw.line((19, 18, 45, 46), fill="white", width=6)
        draw.line((45, 46, 45, 18), fill="white", width=6)
        return image


def build_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem("打开今日简报", lambda icon, item: open_digest(), default=True),
        pystray.MenuItem("立即检查更新", lambda icon, item: CHECK_NOW.set()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("开机启动", toggle_startup, checked=lambda item: startup_enabled()),
        pystray.MenuItem("打开配置目录", open_config_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )


def acquire_single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return None
    return handle


def main() -> int:
    global _MUTEX_HANDLE
    config = load_config()
    minimized = "--minimized" in sys.argv

    if "--once" in sys.argv:
        feed, source = fetch_feed(config)
        if feed is None:
            print("no feed")
            return 1
        write_html(feed)
        ok = show_toast(feed)
        print(f"feed={source} count={item_count(feed)} toast={ok}")
        time.sleep(2)
        return 0 if ok else 1

    _MUTEX_HANDLE = acquire_single_instance()
    if _MUTEX_HANDLE is None:
        if not minimized:
            cached = load_cached_feed()
            if cached is not None:
                open_digest(cached)
                show_message_toast(
                    "每日简报已在运行",
                    "已打开今日简报；程序继续在右下角托盘运行。",
                )
            else:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "每日简报已经在运行。\n请查看右下角托盘图标（可能在 ^ 展开区）。",
                    "每日简报",
                    0x40,
                )
        return 0

    first_run = not STATE_PATH.exists()
    if first_run and config.get("start_with_windows", True):
        set_startup(True)

    icon = pystray.Icon(APP_NAME, load_icon(), APP_TITLE, menu=build_menu())
    threading.Thread(target=poll_loop, args=(icon,), daemon=True).start()
    if not minimized:
        show_manual_start_feedback()
    icon.run()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        detail = traceback.format_exc()
        log(f"fatal startup error:\n{detail}")
        if "--minimized" not in sys.argv:
            try:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"每日简报启动失败。\n\n{detail[-1200:]}\n\n日志：{LOG_PATH}",
                    "每日简报 - 启动失败",
                    0x10,
                )
            except Exception:
                pass
        raise SystemExit(1)
