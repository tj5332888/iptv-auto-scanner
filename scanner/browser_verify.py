"""L4 浏览器播放验证：无头浏览器 + hls.js 实播，判定「真可播」。

这是验证体系的最高层级：
  L1 curl 拉 m3u8/分片     → 流活着
  L2 ffprobe 解码分片      → 编码完好
  L3 hls.js 真实播放       → 播放器可用  ← 本模块
判据：video.currentTime > 0 且 videoWidth > 0（连续 2 次采样）。

依赖：本机有 Edge/Chrome + pip install websocket-client + 可访问 cdn.jsdelivr.net（hls.js）。
GitHub Actions：ubuntu runner 有 chromium，可通过 --browser chromium 使用。
"""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

BROWSERS = [
    ("edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ("edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ("chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ("chromium", "/usr/bin/chromium-browser"),
    ("chromium", "/usr/bin/chromium"),
]

_HLS_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hls.min.js")
_HLS_CDN = "https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"

_HLS_CDN = "https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"


def _find_browser():
    for _, path in BROWSERS:
        if os.path.exists(path):
            return path
    # Linux CI: PATH 查找
    for b in ("chromium-browser", "chromium", "google-chrome"):
        w = shutil.which(b)
        if w:
            return w
    return None


def _make_test_html(url: str, path: str):
    # 注意：hls.js 的 XHR 需要 CORS；最终 CDN 通常 ACAO:*。页面必须从 http:// 提供
    # （file:// 下 Origin=null 会失败），所以 browser_test 会启动本地 HTTP 服务。
    # hls.js 优先用本地文件（避免 CDN 被墙），无本地文件时用 CDN。
    src = "hls.min.js" if os.path.exists(_HLS_LOCAL) else _HLS_CDN
    H = ('<!DOCTYPE html><html><body><video id=v muted autoplay controls></video>'
         '<pre id=log></pre>'
         '<script src="%s"></script>' % src)
    H += '<script>const U=' + json.dumps(url) + ';'
    H += 'const v=document.getElementById("v"),L=document.getElementById("log");'
    H += 'function w(s){L.textContent+=s+"\\n";}'
    H += ('if(window.Hls&&Hls.isSupported()){const h=new Hls();'
          'h.on(Hls.Events.MANIFEST_PARSED,()=>w("MANIFEST_OK"));'
          'h.on(Hls.Events.FRAG_LOADED,()=>w("FRAG"));'
          'h.on(Hls.Events.ERROR,(e,d)=>w("ERR "+d.type+" "+(d.details||"")));'
          'h.loadSource(U);h.attachMedia(v);}'
          'else if(v.canPlayType("application/vnd.apple.mpegurl")){v.src=U;w("NATIVE");}'
          'else{w("NO_HLS");}')
    H += ('setInterval(()=>w("STATE t="+v.currentTime.toFixed(1)+" rs="+v.readyState'
          '+" "+v.videoWidth+"x"+v.videoHeight),2500);')
    H += '</script></body></html>'
    with open(path, "w", encoding="utf-8") as f:
        f.write(H)


def _serve_dir(directory: str, port: int):
    """起一个极简本地 HTTP 服务器（daemon 线程）。返回 (server, thread)。"""
    import http.server
    import threading

    class QH(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = lambda *a, **k: QH(*a, directory=directory, **k)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, th


def browser_test(url: str, port: int, wait: int = 18) -> dict:
    """返回 {ok, time_s, res, frags, errs, detail}"""
    browser = _find_browser()
    if not browser:
        return {"ok": False, "detail": "no browser"}
    tmp = os.path.dirname(os.path.abspath(__file__))
    html_name = f"_bt{port}.html"
    html = os.path.join(tmp, html_name)
    _make_test_html(url, html)
    # 本地 HTTP 服务（file:// 会导致 CORS 失败）
    http_port = port + 5000
    srv, _th = _serve_dir(tmp, http_port)
    page_url = f"http://127.0.0.1:{http_port}/{html_name}"
    prof = os.path.join(tmp, f"_btprof{port}")
    shutil.rmtree(prof, ignore_errors=True)
    p = subprocess.Popen(
        [browser, "--headless=new", "--disable-gpu", "--no-first-run",
         "--ignore-certificate-errors", "--disable-web-security",
         "--allow-running-insecure-content",
         "--no-default-browser-check", f"--remote-debugging-port={port}",
         f"--user-data-dir={prof}", "--autoplay-policy=no-user-gesture-required",
         "--mute-audio", "--window-size=640,360", page_url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(15):
            time.sleep(1)
            try:
                tabs = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=3))
                for t in tabs:
                    if t.get("type") == "page":
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                pass
        if not ws_url:
            return {"ok": False, "detail": "no-tab"}
        import websocket
        ws = websocket.create_connection(ws_url, timeout=10)
        mid = [0]

        def cmd(m, pr=None):
            mid[0] += 1
            ws.send(json.dumps({"id": mid[0], "method": m, "params": pr or {}}))
            return mid[0]

        def ev(expr, wait=8):
            i = cmd("Runtime.evaluate",
                    {"expression": expr, "returnByValue": True})
            t0 = time.time()
            while time.time() - t0 < wait:
                try:
                    msg = json.loads(ws.recv())
                except Exception:
                    return None
                if msg.get("id") == i:
                    return msg.get("result", {}).get("result", {}).get("value")
            return None

        time.sleep(wait)
        log = ev("document.getElementById('log').textContent") or ""
        frags = log.count("FRAG")
        errs = log.count("ERR")
        states = re.findall(r"STATE t=([\d.]+) rs=(\d+) (\d+)x(\d+)", log)
        t_play, res = 0.0, ""
        if states:
            t_play = float(states[-1][0])
            res = f"{states[-1][2]}x{states[-1][3]}"
        ws.close()
        ok = t_play > 0 and frags >= 2 and errs == 0
        return {"ok": ok, "time_s": t_play, "res": res,
                "frags": frags, "errs": errs,
                "detail": (states[-1] if states else log[:100])}
    finally:
        try:
            p.kill()
        except Exception:
            pass
        try:
            srv.shutdown()
        except Exception:
            pass
        shutil.rmtree(prof, ignore_errors=True)
        if os.path.exists(html):
            os.remove(html)


def browser_test_many(items, start_port=9700, max_test=6):
    """items: [(name, result_dict)]，取速度最快的 max_test 条做浏览器验证。
    返回 [(name, result, browser_result)]"""
    import concurrent.futures
    out = []
    items = sorted(items, key=lambda x: -x[1]["speed"])[:max_test]
    for idx, (nm, r) in enumerate(items):
        br = browser_test(r["url"], start_port + idx)
        out.append((nm, r, br))
    return out
