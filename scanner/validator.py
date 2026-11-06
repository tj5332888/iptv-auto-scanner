"""三层验证器：带严格全局时限与预筛，确保按时完成。

关键设计（解决「3 小时跑不完」）：
  1. 预筛：先只拉 m3u8（轻量 15KB），剔除大半死流，再对幸存者做分片测速
  2. 硬超时：requests timeout 双值 (connect, read)，避免慢流拖死
  3. 全局 deadline：验证阶段总时长受控，到点立即汇总已有结果
  4. 变体优选：master 清单选 BANDWIDTH 最高档（75 寸画质优先）
  5. 编码识别：H.264 与 HEVC(H.265) 均识别（HEVC SPS 是 0x42/0x40 NAL）
"""
import re
import time
import concurrent.futures
from urllib.parse import urljoin

import requests

UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 9)"}

_H264_PAT = re.compile(rb"\x00\x00\x00\x01\x67|\x00\x00\x01\x67")
_HEVC_PAT = re.compile(rb"\x00\x00\x00\x01\x42|\x00\x00\x01\x42")  # 只认 SPS(0x42)；VPS(0x40) 头结构不同会读错位
_PROFILE = {66: "Baseline", 77: "Main", 88: "Extended", 100: "High",
            110: "High422", 122: "High10"}
_HEVC_PROFILE = {1: "HEVC-Main", 2: "HEVC-Main10", 3: "HEVC-Still",
                 4: "HEVC-RExt"}


def _get(url, timeout, n=700_000):
    # (connect, read) 双超时：连接 4s、读取 timeout。
    # 注意 read timeout 只管「单次 recv」，挡不住每 4s 吐 1 字节的慢滴流——
    # 因此用 iter_content 分块读 + 总时限 hard_cap，超时立即放弃。
    hard_cap = 12.0
    r = requests.get(url, headers=UA, timeout=(4, timeout), stream=True)
    chunks, got = [], 0
    t0 = time.time()
    try:
        for c in r.iter_content(chunk_size=8192):
            chunks.append(c)
            got += len(c)
            if got >= n or time.time() - t0 > hard_cap:
                break
    finally:
        r.close()
    data = b"".join(chunks)[:n]
    return data, r.url


def _unescape(rbsp: bytes) -> bytes:
    """去防竞争字节（0x00 0x00 0x03 → 0x00 0x00），保证偏移正确。"""
    return rbsp.replace(b"\x00\x00\x03", b"\x00\x00")


def _codec_info(data: bytes):
    """识别编码 → (codec, profile, level)。H.264 SPS / HEVC VPS+SPS 均支持。"""
    m = _H264_PAT.search(data)
    if m:
        i = m.start() + (4 if len(m.group(0)) == 5 else 3)
        try:
            rbsp = _unescape(data[i + 1:i + 8])
            return "H.264", _PROFILE.get(rbsp[0], str(rbsp[0])), rbsp[2] / 10.0
        except IndexError:
            return "H.264", "?", 0.0
    m = _HEVC_PAT.search(data)
    if m:
        i = m.start() + (4 if len(m.group(0)) == 5 else 3)
        try:
            # NAL 头 2 字节后 RBSP：[1]=profile flags [12]=level_idc
            rbsp = _unescape(data[i + 2:i + 24])
            pidc = rbsp[1] & 0x1F
            return "H.265", _HEVC_PROFILE.get(pidc, f"HEVC-{pidc}"), rbsp[12] / 30.0
        except IndexError:
            return "H.265", "HEVC", 0.0
    return None


def _pick_variant(text: str):
    """master 清单 → (url, bandwidth, resolution)。选 BANDWIDTH 最高档。"""
    best, bw_b, res_b = None, -1, ""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", ln)
            bw = int(m.group(1)) if m else 0
            rm = re.search(r"RESOLUTION=(\d+x\d+)", ln)
            res = rm.group(1) if rm else ""
            for ln2 in lines[i + 1:]:
                t = ln2.strip()
                if t and not t.startswith("#"):
                    if bw > bw_b:
                        best, bw_b, res_b = t, bw, res
                    break
    return best, bw_b, res_b


def prescreen(url: str, timeout: int = 4) -> bool:
    """预筛：m3u8 可达且含 EXTM3U。轻量（15KB）。短超时 (3,4) 保证秒级判定。"""
    try:
        data, _ = _get(url, min(timeout, 4), 15_000)
        return b"#EXTM3U" in data
    except Exception:
        return False


def _media_seq(text: str):
    m = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
    return m.group(1) if m else None


def _first_seg(text: str):
    for ln in text.splitlines():
        t = ln.strip()
        if t and not t.startswith("#"):
            return t
    return ""


def validate_one_full(url: str, timeout: int = 6, seg_bytes: int = 15000):
    """完整验证（预筛通过后调用）：master 展开（选最高档）→ 分片 → 编码识别 + 测速。

    单流硬时限 30s：读超时挡不住「连上但龟速」的流（如 45.192.x 那类，
    每分片都要耗满 read timeout），必须用总时限兜底防拖死线程池。
    """
    t0 = time.time()

    def _over():
        return time.time() - t0 > 30

    try:
        data, final = _get(url, timeout, 18_000)
        text = data.decode("utf-8", "ignore")
        if "#EXTM3U" not in text:
            return None
        depth = 0
        v_bw, v_res = 0, ""
        disc = 0
        while "#EXT-X-STREAM-INF" in text and depth < 2:
            if _over():
                return None
            var, vbw, vres = _pick_variant(text)   # ← 选 BANDWIDTH 最高档（画质优先）
            if not var:
                return None
            v_bw, v_res = vbw, vres
            # urljoin 正确处理 http 绝对 / 根绝对(/xxx) / 相对路径 三种形态
            nxt = urljoin(final, var)
            data, final = _get(nxt, timeout, 18_000)
            text = data.decode("utf-8", "ignore")
            if "#EXTM3U" not in text:
                return None
            depth += 1
        disc += text.count("#EXT-X-DISCONTINUITY")
        # 伪直播检测：3 秒后窗口必须滑动（MEDIA-SEQUENCE 或首个分片变化），
        # 否则播放器会无限循环同一段；ENDLIST(VOD化)在直播场景同样必然循环。
        seq1 = _media_seq(text)
        first1 = _first_seg(text)
        time.sleep(3.2)
        if _over():
            return None
        data, final = _get(final, timeout, 18_000)
        text = data.decode("utf-8", "ignore")
        if "#EXTM3U" not in text or "#EXT-X-ENDLIST" in text:
            return None
        seq2 = _media_seq(text)
        first2 = _first_seg(text)
        moved = (seq1 != seq2) if (seq1 is not None and seq2 is not None) \
            else (first1 != first2)
        if not moved:
            # 二次确认（长分片窗口一个 sleep 内可能没有新分片，避免误杀）
            time.sleep(4.5)
            if _over():
                return None
            data, final = _get(final, timeout, 18_000)
            text = data.decode("utf-8", "ignore")
            if "#EXTM3U" not in text or "#EXT-X-ENDLIST" in text:
                return None
            seq3 = _media_seq(text)
            first3 = _first_seg(text)
            moved = (seq1 != seq3) if (seq1 is not None and seq3 is not None) \
                else (first1 != first3)
            if not moved:
                return None
        segs = [l.strip() for l in text.splitlines()
                if l.strip() and not l.startswith("#")]
        if not segs:
            return None
        base = final.rsplit("/", 1)[0]
        seg_url = urljoin(final, segs[0])
        if _over():
            return None
        t1 = time.time()
        seg, _ = _get(seg_url, timeout, 900_000)
        dl = time.time() - t1
        if len(seg) < seg_bytes:
            return None
        codec = _codec_info(seg)
        dur = re.search(r"#EXTINF:([\d.]+)", text)
        kbps = len(seg) * 8 / float(dur.group(1)) / 1000 if dur else 0
        speed = (len(seg) / 1024) / max(dl, 0.1)
        return {"url": url, "seg_url": seg_url,
                "codec": codec[0] if codec else "?",
                "profile": codec[1] if codec else "?",
                "level": codec[2] if codec else 0,
                "vres": v_res, "vbw": v_bw, "disc": disc,
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
    """同名频道去重：速度降序，同速时零 DISCONTINUITY（无时间戳跳变）优先。"""
    best = {}
    for r in sorted(results, key=lambda x: (-x["speed"], x.get("disc", 0))):
        key = re.sub(r"\s+", "", r["name"])
        best.setdefault(key, []).append(r)
    return best
