"""官方直播页抓流器：通过无头浏览器 CDP 截获官方页面播放器请求的真实 m3u8。

支持：央视网(tv.cctv.com) / 央视频(yangshipin) / 芒果TV / 荔枝 / 新蓝网等。
要求：本机有 Edge/Chrome；GitHub Actions 里默认无浏览器会自动跳过。
输出：[(name, url)] —— 截获的 m3u8（带 Referer 要求的，播放器一般也能播）。
"""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

CDP_PORT_BASE = 9600
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _find_browser():
    for p in BROWSER_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _wait_tab(port, keyword, tries=20):
    for _ in range(tries):
        time.sleep(1)
        try:
            tabs = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json", timeout=3))
            for t in tabs:
                if t.get("type") == "page" and keyword in t.get("url", ""):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
    return None


def capture_page(url: str, port: int, wait: int = 30) -> list:
    """打开页面并截获所有 .m3u8 请求。返回 [url,...]"""
    try:
        import websocket  # websocket-client
    except ImportError:
        return []
    browser = _find_browser()
    if not browser:
        return []
    prof = os.path.join(os.environ.get("TEMP", "/tmp"),
                        f"iptvcap{port}")
    shutil.rmtree(prof, ignore_errors=True)
    p = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu", "--no-first-run",
         "--no-default-browser-check", f"--remote-debugging-port={port}",
         f"--user-data-dir={prof}", "--autoplay-policy=no-user-gesture-required",
         url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = _wait_tab(port, re.escape(url.split("/")[2]))
        if not ws_url:
            return []
        ws = websocket.create_connection(ws_url, timeout=10)
        mid = [0]

        def send(m, pr=None):
            mid[0] += 1
            ws.send(json.dumps({"id": mid[0], "method": m, "params": pr or {}}))
            return mid[0]

        send("Network.enable")
        found = []
        t0 = time.time()
        while time.time() - t0 < wait:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                break
            if msg.get("method") == "Network.requestWillBeSent":
                u = msg["params"]["request"]["url"]
                if ".m3u8" in u:
                    found.append(u)
        return found
    finally:
        try:
            ws.close()
        except Exception:
            pass
        p.kill()


def collect_official(cfg_official, start_port=CDP_PORT_BASE):
    """遍历 official_pages 配置。返回 [(name, url)]。
    每页只取第一个 m3u8（官方页首个请求即默认清晰度）。
    环境无浏览器时返回 []（GitHub Actions 安全）。"""
    out = []
    port = start_port
    for site in cfg_official or []:
        base = site.get("base", "")
        pages = site.get("pages") or []
        name = site.get("name", base)
        if pages:
            for pg in pages:
                url = base.rstrip("/") + "/" + pg.strip("/") + "/"
                found = capture_page(url, port)
                port += 1
                if found:
                    out.append((f"{name}-{pg}", found[0]))
        else:
            found = capture_page(base, port)
            port += 1
            if found:
                out.append((name, found[0]))
    return out
