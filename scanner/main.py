#!/usr/bin/env python3
"""主流程：采集 → 验证 → 分类 → 整合 → 输出。

用法：python scanner/main.py [--config config.yaml] [--out output] [--profile fast|full]
"""
import os
import re
import sys
import json
import argparse
import datetime

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collectors import collect_upstream, collect_platforms   # noqa: E402
from validator import validate_many, dedup_keep_fastest      # noqa: E402
from classifier import classify                              # noqa: E402
from builder import build_m3u, build_txt, build_report       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def apply_github_proxy(cfg: dict):
    """本地中国网络时给 githubusercontent 上游加代理前缀（gh-proxy.com）。"""
    if not cfg.get("use_github_proxy"):
        return
    proxy = cfg.get("github_proxy", "").rstrip("/")
    if not proxy:
        return
    for up in cfg.get("upstreams", []):
        if "githubusercontent.com" in up["url"] and not up["url"].startswith(proxy):
            up["url"] = proxy + up["url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output"))
    ap.add_argument("--profile", default="fast", choices=["fast", "full"],
                    help="fast=核心上游（Actions 安全） full=全部上游")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    apply_github_proxy(cfg)
    vcfg = cfg.get("validate", {})
    timeout = vcfg.get("timeout", 8)
    seg_bytes = vcfg.get("seg_bytes", 15000)
    min_speed = vcfg.get("min_speed_kb", 150)
    min_kbps = vcfg.get("min_kbps", 800)
    workers = vcfg.get("workers", 12)
    max_per = vcfg.get("max_per_source", 400)
    epg = cfg.get("epg", "")

    upstreams = cfg.get("upstreams", [])
    if args.profile == "fast":
        fast_names = {"hacks-cctv", "hacks-ws", "bestfan-cctv",
                      "zbds-iptv4", "guovin"}
        upstreams = [u for u in upstreams if u["name"] in fast_names]

    now = datetime.datetime.now()
    stats = {"time": now.strftime("%F %T")}

    seen = set()
    pool = []          # (name, url, source)

    # 1) 平台直播（抖音/B站代理）
    for plat, cfgp in (cfg.get("platforms") or {}).items():
        for n, u in collect_platforms({plat: cfgp}):
            if u not in seen:
                seen.add(u)
                pool.append((n, u, f"platform:{plat}"))
    print(f"[platforms] {len(pool)} channels", flush=True)

    # 2) 上游聚合（只收 CCTV/卫视/CGTN 相关 —— 关键提速：避免地方台噪音拖满验证时限）
    name_pat = re.compile(r"CCTV|央视|卫视|CGTN", re.I)
    for up in upstreams:
        items = collect_upstream(up["name"], up["url"], up["type"])[:max_per * 3]
        fresh = [(n, u) for n, u in items if u not in seen and name_pat.search(n)]
        for n, u in fresh:
            seen.add(u)
            pool.append((n, u, up["name"]))
        print(f"[upstream] {up['name']:<16} {len(fresh):>4} fresh (of {len(items)})", flush=True)
    stats["collected"] = len(pool)

    # 3) 官方直播页（仅本地有浏览器时生效；Actions 环境自动跳过）
    if not os.environ.get("CI"):
        try:
            from pages import collect_official
            off = collect_official(cfg.get("official_pages"))
            for n, u in off:
                if u not in seen:
                    seen.add(u)
                    pool.append((n, u, "official-page"))
            print(f"[official] {len(off)} streams", flush=True)
        except Exception as e:
            print(f"[official] skipped ({str(e)[:40]})", flush=True)

    # 4) 两阶段验证（带来源，全局时限防拖死）
    deadline = vcfg.get("deadline_s", 900)   # 验证阶段总时限（秒）
    print(f"[validate] {len(pool)} candidates, deadline={deadline}s", flush=True)
    results = validate_many([(n, u) for n, u, _ in pool],
                            timeout, seg_bytes, workers, deadline)
    url2src = {u: src for n, u, src in pool}
    for r in results:
        r["source"] = url2src.get(r["url"], "unknown")
    stats["valid"] = len(results)
    stats["dropped"] = len(pool) - len(results)
    print(f"[validate] pass={len(results)} drop={stats['dropped']}", flush=True)

    # 5) 阈值过滤（75寸质量标准）
    results = [r for r in results
               if r["speed"] >= min_speed and (min_kbps == 0 or r["kbps"] >= min_kbps)]
    print(f"[filter] quality-gate: {len(results)}", flush=True)

    # 5.5) L4 浏览器播放验证（对速度 Top N 实播；无浏览器/CI 时自动跳过）
    bvcfg = cfg.get("browser_verify") or {}
    l4_results = []
    if bvcfg.get("enabled") and not os.environ.get("CI"):
        try:
            from browser_verify import browser_test
            top = sorted(results, key=lambda x: -x["speed"])[:bvcfg.get("max_test", 6)]
            print(f"[browser-l4] testing top {len(top)} ...", flush=True)
            for idx, r in enumerate(top):
                port = bvcfg.get("start_port", 9700) + idx
                br = browser_test(r["url"], port, bvcfg.get("wait", 18))
                r["browser_ok"] = br.get("ok", False)
                r["browser"] = br
                mark = "PLAY" if br.get("ok") else "fail"
                print(f"[browser-l4] {mark} t={br.get('time_s',0)}s "
                      f"res={br.get('res','?')} frags={br.get('frags',0)} "
                      f"errs={br.get('errs',0)}  {r['name'][:24]}", flush=True)
                l4_results.append((r["name"], r, br))
            if l4_results:
                stats["l4_pass"] = sum(1 for _, _, b in l4_results if b.get("ok"))
                print(f"[browser-l4] pass={stats['l4_pass']}/{len(l4_results)}", flush=True)
        except Exception as e:
            print(f"[browser-l4] skipped ({str(e)[:40]})", flush=True)

    # 6) 去重 + 分类整合
    dedup = dedup_keep_fastest(results)

    def ch_num(key):
        m = re.search(r"\d+", key)
        return int(m.group()) if m else 999

    categorized = {}
    seen_ch = set()
    ordered = sorted(dedup.keys(),
                     key=lambda k: (classify(k, cfg.get("categories", {})) != "央视频道",
                                    ch_num(k), k))
    for key in ordered:
        chans = sorted(dedup[key], key=lambda x: -x["speed"])
        r = chans[0]
        cat = classify(r["name"], cfg.get("categories", {}))
        ch_key = re.sub(r"[\s(（【].*$", "", r["name"])
        if ch_key in seen_ch:
            continue
        seen_ch.add(ch_key)
        categorized.setdefault(cat, []).append((r["name"], r, chans))
    for cat in categorized:
        categorized[cat].sort(key=lambda x: -x[1]["speed"])

    # 7) 输出
    if not args.dry:
        os.makedirs(args.out, exist_ok=True)
        m3u = build_m3u(categorized, epg)
        txt = build_txt(categorized)
        rep = build_report(categorized, stats)
        open(os.path.join(args.out, "live.m3u"), "w", encoding="utf-8").write(m3u)
        open(os.path.join(args.out, "live.txt"), "w", encoding="utf-8").write(txt)
        open(os.path.join(args.out, "report.md"), "w", encoding="utf-8").write(rep)
        # 来源清单（完整溯源：主线 + 备用）
        src = [f"# 来源清单 {stats['time']}", ""]
        for cat, chans in categorized.items():
            src.append(f"## {cat}")
            for name, r, all_lines in chans:
                src.append(f"- {name} ← {r.get('source','?')}"
                           + (f"（另 {len(all_lines)-1} 条备用）" if len(all_lines) > 1 else ""))
                src.append(f"  主线: {r['url']}")
                for alt in all_lines[1:3]:
                    src.append(f"  备用: {alt['url']}")
            src.append("")
        open(os.path.join(args.out, "sources.md"), "w", encoding="utf-8").write("\n".join(src))
        json.dump({"time": stats["time"],
                   "channels": sum(len(v) for v in categorized.values()),
                   "lines": sum(len(v) for v in dedup.values()),
                   "valid": stats["valid"], "collected": stats["collected"]},
                  open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"[out] written to {args.out}", flush=True)
    else:
        for cat, chans in categorized.items():
            print(f"  {cat}: {len(chans)}")

    print(f"[done] {sum(len(v) for v in categorized.values())} channels / "
          f"{sum(len(v) for v in dedup.values())} lines", flush=True)


if __name__ == "__main__":
    main()
