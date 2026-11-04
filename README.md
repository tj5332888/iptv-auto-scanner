# IPTV 直播源自动扫描验证系统

每 6 小时自动扫描全网直播源，三层验证（可用性→速度→编码档位），按频道分类整合，输出带 EPG 的 M3U/TXT。master 多码率清单自动选最高档；H.264 与 HEVC(H.265) 编码均识别，HEVC 流按编码效率自适应画质门槛。

## 数据来源声明（Credits / Sources）

本项目**不存储、不生产任何直播流内容**，仅对下列公开项目的直播源地址做聚合、验证与格式整理。所有频道的版权归原播出机构，直播源 URL 的收集与维护由以下开源项目完成，向这些项目的维护者致敬：

| 上游项目 | 仓库 / 站点 | 本项目用途 |
|---|---|---|
| **vbskycn/iptv** | [GitHub](https://github.com/vbskycn/iptv) ｜ [镜像站](https://live.zbds.top/) | 每 6 小时自动扫描验证的 IPv4 直播源（iptv4.txt / iptv4.m3u），本项目最大的上游 |
| **best-fan/iptv-sources** | [GitHub](https://github.com/best-fan/iptv-sources) | 每日 Jenkins 自动构建的 cn_cctv / cn_all / cn_province 分类源 |
| **Guovin/iptv-api** | [GitHub](https://github.com/Guovin/iptv-api) | 自动测效聚合源 result.m3u |
| **YueChan/Live** | [GitHub](https://github.com/YueChan/Live) | 人工维护补充源 |
| **suxuang/myIPTV** | [GitHub](https://github.com/suxuang/myIPTV) | IPv4 直播源（ipv4.m3u） |
| **FoodieGuide IPTV Hub** | [站点](http://www.foodieguide.com/iptv/) | 综合源 hub001-foodies.txt |
| **tonkiang.us** | [站点](http://tonkiang.us) | HLS 源检索列表 |
| **iptv-org/iptv** | [GitHub](https://github.com/iptv-org/iptv) ｜ [站点](https://iptv-org.github.io/iptv/) | 全球公开 IPTV 频道数据库（cn 分类） |
| **qist/tvbox** | [GitHub](https://github.com/qist/tvbox) ｜ [镜像](https://qist.wyfc.qzz.io/) | tvboxtv.txt 综合源 |
| **YanG-1989/m3u** | [GitHub](https://github.com/YanG-1989/m3u) | 直播平台代理解析服务（douyin/bilibili 路径），本项目的平台直播间方案基于该服务的公开接口 |
| **epg.cdn.loc.cc** | [站点](https://epg.cdn.loc.cc) | EPG 节目单数据（diyp 接口格式） |

**致谢排序不分先后。** 如果你是上述项目的维护者且不希望被本项目引用，请提 Issue，我会移除对应上游。

### 各上游 License

- vbskycn/iptv：MIT
- iptv-org/iptv：CC0（Unlicense）
- best-fan/iptv-sources：MIT
- 其余项目以其仓库标注为准

## 功能

- **全网采集**：zbds / hacks.tools / best-fan / Guovin 等聚合站 + 自定义上游
- **直播平台**：抖音 / B站 / 虎牙 / 斗鱼 直播间代理解析（基于 yang-1989 服务模式）
- **三层验证**：m3u8 可达 → 分片下载 → 编码档位解析（H.264 SPS / HEVC SPS 均识别）+ 测速；master 清单选 BANDWIDTH 最高档
- **智能分类**：央视 / 卫视 / 体育 / 电影 / 地方 / 纪录 / 其他
- **质量评级**：按码率和速度排序，同频道多线路备用；HEVC 流画质门槛自动减半（同画质省一半码率）
- **双格式输出**：M3U（带台标+EPG）+ TXT（TVBox 格式）
- **GitHub Actions**：每 6 小时自动运行并提交更新

## 使用

1. Fork 或推送到你的 GitHub 仓库
2. Actions 会自动每 6 小时运行（也可手动触发）
3. 从 `output/` 目录取 `live.m3u` 或 `live.txt` 填入 TVBox

## 来源追踪（M3U 内嵌）

输出的 `live.m3u` 中每条频道行都会在注释里标注来源，例如：

```m3u
#EXTINF:-1 tvg-id="CCTV13" tvg-name="CCTV13" group-title="央视频道",CCTV-13新闻 [H.264 High@4.0 1080p 2783KB/s]
# source: bestfan-cctv | quality: 1400kbps
https://event.pull.hebtv.com/jishi/cp1.m3u8
```

`report.md` 也会按分类列出每条频道的完整 URL 与编码信息，便于追溯。

## L4 浏览器实播验证

在三层验证之上，对速度 Top 6 频道用无头 Edge + hls.js 做真实播放测试（`browser_verify` 配置开启，CI 环境自动跳过），判据：播放进度推进 + 加载 ≥2 分片 + 零错误。3 路并发，约 30 秒完成。

## 添加自定义频道

编辑 `config.yaml` 的 `platforms` 段：

```yaml
platforms:
  douyin:
    - { name: 央视网快看, id: kuaikancctv }
    - { name: 央视网财经, id: nuanxinnengl }
  bilibili:
    - { name: 央视网, id: "8178490" }
```

## 配置说明

| 配置项 | 说明 |
|---|---|
| `min_speed_kb` | 最低速度阈值（KB/s），低于此值淘汰 |
| `min_kbps` | 最低码率阈值（kbps） |
| `timeout` | 单条流验证超时（秒） |
| `categories` | 频道名 → 分类匹配规则（正则） |

## License

MIT
