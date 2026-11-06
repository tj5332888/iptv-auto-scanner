"""输出构建：生成分类整合的 M3U（台标+EPG 属性）和 TVBox TXT。

categorized 结构：{分类: [(name, result_dict, all_lines), ...]}
  result_dict 含 source 字段（溯源）+ url/profile/level/kbps/speed
"""
import re


def _safe(s: str) -> str:
    return re.sub(r"[,\r\n]", " ", s or "")


def build_m3u(categorized: dict, epg_template: str = "") -> str:
    out = ["#EXTM3U"]
    if epg_template:
        out[0] += f' x-tvg-url="{epg_template}"'
    out.append("# 数据来源：聚合自下列公开项目（详见 sources.md）：")
    out.append("# vbskycn/iptv | best-fan/iptv-sources | Guovin/iptv-api")
    out.append("# YueChan/Live | iptv-org/iptv | qist/tvbox | YanG-1989/m3u (平台代理)")
    for cat, chans in categorized.items():
        out.append(f'#PLAYLIST:{cat}')
        for item in chans:
            name, r = item[0], item[1]
            tvg_id = re.sub(r"\s+", "", name)
            src = r.get("source", "")
            codec = r.get("codec", "?")
            tag = f"{codec} {r['profile']}@{r['level']:.1f}"
            if r.get("vres"):
                tag += f" {r['vres']}"
            out.append(
                f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{_safe(name)}" '
                f'group-title="{cat}",{name} [{tag} {r["speed"]}KB/s]'
            )
            if src:
                out.append(f'# source: {src} | quality: {r["kbps"]}kbps')
            out.append(r["url"])
    return "\n".join(out) + "\n"


def build_txt(categorized: dict) -> str:
    out = []
    for cat, chans in categorized.items():
        out.append(f"{cat},#genre#")
        for item in chans:
            name, r = item[0], item[1]
            out.append(f"{_safe(name)},{r['url']}")
        out.append("")
    return "\n".join(out) + "\n"


def _h264_only(categorized: dict) -> dict:
    """只保留 H.264 线路的频道（HEVC/未知编码剔除）。

    海信等电视端系统播放器对 HTTP TS 封装的 H.265 支持差（黑屏），
    TVBox 走 exoplayer 内核同样踩坑，电视端专用清单只输出 H.264。
    """
    out = {}
    for cat, chans in categorized.items():
        keep = []
        for item in chans:
            name, r, all_lines = item[0], item[1], item[2]
            lines264 = [x for x in all_lines if x.get("codec") == "H.264"]
            if not lines264:
                continue
            keep.append((name, lines264[0], lines264))
        if keep:
            out[cat] = keep
    return out


def build_tvbox_m3u(categorized: dict, epg_template: str = "") -> str:
    """电视端（TVBox/海信）安全版：纯 H.264，剔除 HEVC 与未知编码。"""
    return build_m3u(_h264_only(categorized), epg_template)


def build_tvbox_txt(categorized: dict) -> str:
    """电视端（TVBox/海信）安全版 TXT：纯 H.264。"""
    return build_txt(_h264_only(categorized))


def build_report(categorized: dict, stats: dict) -> str:
    out = [f"# IPTV 扫描报告 {stats.get('time','')}", ""]
    out.append(f"- 上游采集：{stats.get('collected',0)} 条")
    out.append(f"- 验证通过：{stats.get('valid',0)} 条")
    out.append(f"- 淘汰：{stats.get('dropped',0)} 条")
    if "l4_pass" in stats:
        out.append(f"- L4 浏览器实播：{stats['l4_pass']} 条确认")
    out.append("")
    out.append("## 上游项目致谢")
    out.append("")
    for s in ["vbskycn/iptv (live.zbds.top)", "best-fan/iptv-sources",
              "Guovin/iptv-api", "YueChan/Live", "iptv-org/iptv",
              "qist/tvbox", "YanG-1989/m3u (平台代理解析)", "epg.cdn.loc.cc (EPG)"]:
        out.append(f"- {s}")
    out.append("")
    for cat, chans in categorized.items():
        out.append(f"## {cat}（{len(chans)} 频道）")
        out.append("")
        out.append("| 频道 | 编码 | 档位 | 码率 | 速度 | 来源 |")
        out.append("|---|---|---|---|---|---|")
        for item in chans:
            name, r = item[0], item[1]
            out.append(f"| {name} | {r.get('codec','?')} | {r['profile']}@{r['level']:.1f} "
                       f"| {r['kbps']}kbps | {r['speed']}KB/s | {r.get('source','?')} |")
        out.append("")
    return "\n".join(out) + "\n"
