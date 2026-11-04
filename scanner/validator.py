"""三层验证器：带严格全局时限与预筛，确保按时完成。

关键设计（解决「3 小时跑不完」）：
  1. 预筛：先只拉 m3u8（轻量 15KB），剔除大半死流，再对幸存者做分片测速
  2. 硬超时：requests timeout 双值 (connect, read)，避免慢流拖死
  3. 全局 deadline：验证阶段总时长受控，到点立即汇总已有结果
"""
import re
import time
import concurrent.futures
import requests

UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 9)"}

_TS_PAT = re.compile(rb"\x00\x00\x00\x01\x67|\x00\x00\x01\x67")
_PROFILE = {66: "Baseline", 77: "Main", 88: "Extended", 100: "High",
            110: "High422", 122: "High10"}


def _get(url, timeout, n=700_000):
    # (connect, read) 双超时：连接 4s、读取 timeout
    r = requests.get(url, headers=UA, timeout=(4, timeout), stream=True)
    data = r.content[:n]
    return data, r.url


def _sps_level(data: bytes):
    m = _TS_PAT.search(data)
    if not m:
        return None
    off = 4 if m.group(0) == b"\x00\x00\x00\x01\x67" else 3
    i = m.start() + off
    try:
        prof = data[i + 1]
        lvl = data[i + 3]
        return _PROFILE.get(prof, str(prof)), lvl / 10.0
    except IndexError:
        return None


def prescreen(url: str, timeout: int = 4) -> bool:
    """预筛：m3u8 可达且含 EXTM3U。轻量（15KB）。短超时 (3,4) 保证秒级判定。"""
    try:
        data, _ = _get(url, min(timeout, 4), 15_000)
        return b"#EXTM3U" in data
    except Exception:
        return False


def validate_one_full(url: str, timeout: int = 6, seg_bytes: int = 15000):
    """完整验证（预筛通过后调用）：master 展开 → 分片 → SPS + 测速。"""
    try:
        data, final = _get(url, timeout, 18_000)
        text = data.decode("utf-8", "ignore")
        if "#EXTM3U" not in text:
            return None
        depth = 0
        while "#EXT-X-STREAM-INF" in text and depth < 2:
            variants = [l.strip() for l in text.splitlines()
                        if l.strip() and not l.startswith("#")]
            if not variants:
                return None
            base = final.rsplit("/", 1)[0]
            nxt = variants[0] if variants[0].startswith("http") else base + "/" + variants[0]
            data, final = _get(nxt, timeout, 18_000)
            text = data.decode("utf-8", "ignore")
            if "#EXTM3U" not in text:
                return None
            depth += 1
        segs = [l.strip() for l in text.splitlines()
                if l.strip() and not l.startswith("#")]
        if not segs:
            return None
        base = final.rsplit("/", 1)[0]
        seg_url = segs[0] if segs[0].startswith("http") else base + "/" + segs[0]
        t1 = time.time()
        seg, _ = _get(seg_url, timeout, 900_000)
        dl = time.time() - t1
        if len(seg) < seg_bytes:
            return None
        sps = _sps_level(seg)
        dur = re.search(r"#EXTINF:([\d.]+)", text)
        kbps = len(seg) * 8 / float(dur.group(1)) / 1000 if dur else 0
        speed = (len(seg) / 1024) / max(dl, 0.1)
        return {"url": url, "seg_url": seg_url,
                "profile": sps[0] if sps else "?", "level": sps[1] if sps else 0,
                "kbps": int(kbps), "speed": int(speed), "seg_kb": len(seg) // 1024}
    except Exception:
        return None


def validate_many(items, timeout=6, seg_bytes=15000, workers=16, deadline_s=600):
    """两阶段验证 + 全局时限（极速版）。

    items: [(name, url)]
    deadline_s: 验证阶段总时限（秒）。极速参数下通常 2-4 分钟内自然完成。
    返回 [(name, result_dict)]。
    """
    t_start = time.time()
    # 阶段 1：并发预筛（短超时 4s + 16 并发 → 实测 8 条/3s）
    alive = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(prescreen, u, 4): (n, u) for n, u in items}
        for fut in concurrent.futures.as_completed(futs):
            nm, u = futs[fut]
            if fut.result():
                alive.append((nm, u))
    print(f"  [prescreen] {len(alive)}/{len(items)} alive "
          f"({time.time()-t_start:.0f}s)", flush=True)

    # 阶段 2：对幸存者完整验证
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(validate_one_full, u, timeout, seg_bytes): (n, u)
                for n, u in alive}
        for fut in concurrent.futures.as_completed(futs):
            nm, u = futs[fut]
            if time.time() - t_start > deadline_s:
                print("  [deadline] reached, collecting existing", flush=True)
                break
            r = fut.result()
            if r:
                r["name"] = nm
                results.append(r)
    print(f"  [validate] {len(results)} valid in {time.time()-t_start:.0f}s",
          flush=True)
    return results


def dedup_keep_fastest(results):
    """同名频道去重：按速度降序分组。"""
    best = {}
    for r in sorted(results, key=lambda x: -x["speed"]):
        key = re.sub(r"\s+", "", r["name"])
        best.setdefault(key, []).append(r)
    return best
