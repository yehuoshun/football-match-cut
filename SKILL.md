---
name: football-match-cut
description: 把一场足球比赛长视频切成可检索的镜头库，再按文案/需求生成剪辑表并用 ffmpeg 精确切割成片。纯画面流程（不依赖解说音），本地先做镜头切分、pHash 去重、记分牌 OCR 定位进球，只把少量帧图交给视觉模型打标，最后输出 EDL 交 ffmpeg 下刀。Use when the user需要：剪足球比赛/集锦/进球瞬间、按文案或口播稿配比赛画面、把 2 小时比赛录像做成高光片段、给长视频建镜头索引、生成 EDL 剪辑表、批量切割比赛片段、统计一条比赛能剪几条视频的 token 成本。
---

# 足球比赛长视频剪辑

纯画面流水线：**本地免费筛 → 少量帧图打标 → 文本匹配 → ffmpeg 下刀**。

核心原则：

- 模型只看**拼图帧**做判断，永远输出**剪辑表（EDL）**，不输出视频
- 进球定位靠**记分牌 OCR**，不靠模型"看球进没进网"（会被越位球骗）
- 镜头库（`shots.json`）建一次、复用到底；文案改版只重跑匹配，不重建库

参数与提示词模板见 `references/prompts.md`。

## 流水线

```
S1 切镜头    split_shots.py   → shots.json
S2 进球定位  score_events.py  → goals.json
S3 出拼图    build_sheets.py  → sheets/*.jpg + sheet_index.json
S4 打标     （视觉模型）      → shots.json 补 scene/usable 字段
S5 文案拆解 （文本模型）      → needs.json
S6 匹配     （文本模型/脚本） → edl.json
S7 切割     cut_edl.py        → clips/ + final.mp4
```

S1–S3、S7 全部本地跑，**0 token**。令牌只花在 S4（一次）和 S5–S6（每版极便宜）。

## 快速开始

```bash
# S1 切镜头（阈值 0.25；镜头 >3000 提到 0.35，<500 降到 0.15）
python scripts/split_shots.py match.mp4 -o work/shots.json --threshold 0.25 --min-dur 1.0

# S2 进球定位（先手动确认 --rect 裁剪框对不对）
python scripts/score_events.py match.mp4 -o work/goals.json --rect 0.0,0.0,0.30,0.10 --fps 0.5

# S3 抽帧拼图（每个镜头 1 帧，4x4 一张）
python scripts/build_sheets.py match.mp4 work/shots.json -o work/sheets --grid 4 --size 512

# S4 把 sheets/ 里的图交给视觉模型，按 references/prompts.md 的打标模板输出，
#    结果写回 shots.json 的 scene / shot / usable / note 字段

# S6 匹配后得到 edl.json，S7 切割成片
python scripts/cut_edl.py match.mp4 work/edl.json -o work/clips --accurate
```

## 关键参数

| 环节 | 参数 | 推荐 | 调整信号 |
|---|---|---|---|
| 切镜头 | `--threshold` | 0.25 | 镜头 >3000 → 0.35；<500 → 0.15 |
| 去重 | `--dedupe-dist` | 6 | 误合并 → 4；漏合并 → 10 |
| 最短镜头 | `--min-dur` | 1.0 | 快切素材 → 0.5 |
| 进球 OCR | `--fps` | 0.5 | 比分档有滚动广告时降到 0.2 |
| 拼图 | `--grid` / `--size` | 4 / 512 | 要看球衣号 → size 1024 |
| 切割精度 | `--accurate` | 开 | 只要快 → 去掉，改 `-c copy` |

## 硬性注意事项

1. **`-ss` 位置决定精度**：放在 `-i` 之后才帧级精确（要重编码）；放前面快但对齐关键帧，可能差半秒。`cut_edl.py --accurate` 已按前者实现。
2. **一个进球常被报 2～3 次**：正片 + 慢放回放 + 另一机位。`score_events.py` 按时间邻近合并，仍要在 EDL 里区分"正片/回放"。
3. **越位进球**：模型说进了不算，只信记分牌。OCR 出的比分变化才是 `T_goal`。
4. **认人不可靠**：远景球衣号码只有十几像素，"剪某球员所有触球"这类需求纯视觉做不到，需另上号码 OCR 或放弃。
5. **拼图 4×4 会丢细节**：一张图 16 个镜头，适合判断场景/景别，不适合判断细节动作；要细节就单帧高分辨率抽事件窗口。
6. **缓存必须开**：文案改版、重复匹配时命中缓存，输入成本约为未命中的 1/10。

## 产出物

```
work/
  shots.json        镜头库（核心资产，长期保存，跨剪辑版本复用）
  goals.json        进球/事件时间轴
  sheets/*.jpg      打标用拼图（可删）
  needs.json        文案拆出的画面需求
  edl.json          剪辑表
  edl.csv           人工校对用
  clips/            成片片段
  final.mp4         拼好的成片
```

## Token 预算（2 小时比赛）

| 阶段 | 用量 |
|---|---|
| S1–S3 本地 | 0 |
| S4 镜头库打标（约 500～700 有效镜头） | 20～40 万 |
| 事件窗口细看（10～15 个 × 300～600 帧） | 15～30 万 |
| S5–S6 文案匹配（每版） | 2～3 万 |
| **首版合计** | **40～75 万** |
| **后续每版** | **2～3 万** |