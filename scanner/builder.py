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
            out.append(
                f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{_safe(name)}" '
                f'group-title="{cat}",{name} [{r["profile"]}@{r["level"]:.1f} {r["speed"]}KB/s]'
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
        out.append("| 频道 | 档位 | 码率 | 速度 | 来源 |")
        out.append("|---|---|---|---|---|")
        for item in chans:
            name, r = item[0], item[1]
            out.append(f"| {name} | {r['profile']}@{r['level']:.1f} "
                       f"| {r['kbps']}kbps | {r['speed']}KB/s | {r.get('source','?')} |")
        out.append("")
    return "\n".join(out) + "\n"
