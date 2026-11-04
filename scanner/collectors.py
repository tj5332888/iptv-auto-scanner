"""上游采集器：从聚合站和直播平台收集原始频道 (name, url)。"""
import re
import requests

UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 9)"}


def _parse_m3u(text: str):
    """解析 M3U → [(name, url)]"""
    out, pending = [], None
    for ln in text.splitlines():
        t = ln.strip()
        if t.startswith("#EXTINF"):
            i = t.rfind(",")
            pending = t[i + 1:].strip() if i >= 0 and i < len(t) - 1 else "?"
        elif t and not t.startswith("#") and "://" in t:
            yield t, pending or "?"
            pending = None


def _parse_txt(text: str):
    """解析 TVBox TXT → [(name, url)]，忽略组头"""
    for ln in text.splitlines():
        t = ln.strip()
        if t.endswith("#genre#") or not t:
            continue
        if "," in t and "://" in t:
            nm, u = t.split(",", 1)
            u = u.strip()
            if u.startswith(("http://", "https://")):
                # 跳过内网/组播
                if re.search(r"(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|\[)", u):
                    continue
                yield u, nm.strip()


def _is_ipv6(url: str) -> bool:
    return bool(re.search(r"//\[[0-9a-fA-F:]+\]", url))


def collect_upstream(name: str, url: str, ftype: str):
    """从单个上游拉取并解析。返回 [(name, url)]"""
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        text = r.text
    except Exception:
        return []
    try:
        gen = _parse_m3u(text) if ftype == "m3u" else _parse_txt(text)
        return [(nm, u) for u, nm in gen if not _is_ipv6(u)]
    except Exception:
        return []


def collect_platforms(platforms_cfg):
    """直播平台代理解析 → [(name, url)]。单个房间失败不影响整体。"""
    out = []
    for plat, cfg in (platforms_cfg or {}).items():
        proxy = cfg.get("proxy", "").rstrip("/")
        for ch in cfg.get("channels", []):
            url = f"{proxy}/{plat}/{ch['id']}"
            out.append((ch["name"], url))
    return out
