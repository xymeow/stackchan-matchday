---
name: market-pairing
description: 为 stackchan-matchday 发现并配对跨平台盘口：拉取 Kalshi、Polymarket、ESPN 的候选事件，模糊匹配出"现实中的同一场比赛/事件"，生成 config/pairing_registry.json 的配对提议供人工确认。当用户说"配对"、"找盘口"、"发现比赛"、"帮我配下周的 MLB/英超/NBA"、"这场比赛 Kalshi/Polymarket 上有没有盘"、"pairing"、"更新 registry"，或提到把某场赛事和预测市场对应起来时，使用本 skill。
---

# Market Pairing（盘口配对助手）

把三类数据源中"现实中的同一件事"对应起来，产出配对注册表提议：

- Kalshi：结构化 event/market ticker（如 `KXWCADVANCE-26JUL15ENGARG-ENG`）
- Polymarket：自由文本标题 + outcome token（Gamma API）
- ESPN：赛程 event id（可选；非体育品类可以没有赛事源）

架构背景见 `docs/multi-venue-roadmap-prd.zh-CN.md` 第 4.3 与第 5 节。
核心原则：**本 skill 只提议，不确认**。watcher 只消费 `confirmed: true`
的条目，而把 `confirmed` 置真的动作必须来自用户明确指示。

## 工作流程

### 1. 明确范围

从用户请求中确定：品类（mlb / tennis / epl / ucl / nba / nfl / 政治等）、
日期窗口（默认未来 7 天）、是否需要 ESPN 赛事源（政治、选秀等品类跳过）。

### 2. 拉取候选（用脚本，不要手写请求）

```sh
python3 scripts/fetch_pairing_candidates.py --source kalshi --days 7 --query "yankees"
python3 scripts/fetch_pairing_candidates.py --source polymarket --tag mlb --days 7
python3 scripts/fetch_pairing_candidates.py --source espn --espn-league baseball/mlb --days 7
```

脚本输出裁剪过的紧凑 JSON（标题、时间、结果、量价字段），避免把原始
API 响应灌进上下文。常用参数速查：

| 品类 | --espn-league | Polymarket --tag | Kalshi 建议 |
| --- | --- | --- | --- |
| MLB | `baseball/mlb` | `mlb` | `--query` 队名 |
| 网球 ATP/WTA | `tennis/atp` / `tennis/wta` | `tennis` | `--query` 球员姓 |
| 英超 | `soccer/eng.1` | `epl` | `--query` 队名 |
| 欧冠 | `soccer/uefa.champions` | `champions-league` | `--query` 队名 |
| NBA | `basketball/nba` | `nba` | `--query` 队名 |
| NFL | `football/nfl` | `nfl` | `--query` 队名 |

Kalshi 的 series ticker 命名随品类扩张变化，不要凭记忆硬编码；
不确定时先 `--source kalshi --list-series --query <运动名>` 查当前
开放的 series，再用 `--kalshi-series` 精确过滤（同一运动会有几十个
series：单场胜负、季后赛、总冠军、球员数据盘等，配对陪看用的是
单场胜负类）。

已验证的数据坑（2026-07 实测）：

- Kalshi 事件标题用**城市名**不用队名（"New York M vs Philadelphia"，
  没有 "Yankees"/"Mets"），`--query` 要用城市名或缩写；`sub_title`
  含缩写和日期（"NYM vs PHI (Jul 16)"）更可靠。
- Kalshi `event_ticker` 内嵌日期、时间与两队缩写
  （`KXMLBGAME-26JUL161910NYMPHI`），是最强的匹配信号，优先解析它。
- Kalshi 的 `close_time` 可能比开赛晚数天（7/16 的比赛 7/19 收盘），
  与 Polymarket `endDate` 同理，都不能当开赛时间用。
- Kalshi 嵌套 markets 视图里 `volume`/`yes_bid` 可能为 null，
  需要量价证据时按单个 ticker 再查 `/markets/<ticker>`。
- Polymarket 的 `endDate` 经常晚于实际开赛时间（结算缓冲或系列赛
  打包），脚本已对时间窗放宽 +3 天；比赛时间以标题和 ESPN 对照为准，
  不要拿 endDate 当开赛时间写进 registry。
- Polymarket 无 `--tag` 时，`--query` 只能在 24h 成交量前 100 的
  事件里搜，冷门比赛会漏——先用 `--tag` 缩小品类再查；同一场比赛
  会同时存在胜负盘和 "Player Props" 等衍生事件，配对只取胜负盘。

### 3. 匹配

对每个候选组合按以下规则打分，全部通过才算高置信：

- **参赛方一致**：队名/人名规范化后匹配。注意别名——ESPN 用
  "New York Yankees"/"NYY"，Polymarket 标题可能只写 "Yankees"，
  Kalshi ticker 用缩写。城市名 + 队名任一匹配即可，但同城多队
  （Yankees/Mets、Lakers/Clippers）必须靠队名区分。
- **时间一致**：开赛时间容差 ±2 小时（跨时区、推迟常见）；
  盘口收盘时间应晚于开赛时间且在赛期内。
- **结果结构一致**：两平台的 outcome 数量与语义对得上
  （胜负盘对胜负盘，别把"晋级盘"配给"单场胜负盘"）。
- **量价合理**：目标市场 status 为 open 且有流动性；已结算或
  空订单簿的候选降级列出。

网球注意：同一轮次同名选手极少但存在（如同姓兄弟），用比赛时间
和赛事名双重确认。政治/选秀等多路盘：outcomes 按候选人列全，
无 event_source 属正常。

### 4. 写入提议

追加或更新 `config/pairing_registry.json`（只碰这个文件，不改
`kalshi_watchlist.json` 等其他配置）。条目格式：

```json
{
  "id": "mlb-2026-07-21-NYY-BOS",
  "category": "mlb",
  "label": { "zh": "扬基 vs 红袜", "en": "Yankees vs Red Sox" },
  "starts_at": "2026-07-21T23:10:00+00:00",
  "outcomes": ["NYY", "BOS"],
  "event_source": { "provider": "espn", "league": "baseball/mlb", "event_id": "401234567" },
  "venue_markets": [
    { "venue": "kalshi", "event_ticker": "…", "outcome_map": { "NYY": "…-NYY", "BOS": "…-BOS" } },
    { "venue": "polymarket", "event_id": "…", "outcome_map": { "NYY": "<token_id>", "BOS": "<token_id>" } }
  ],
  "pairing": {
    "proposed_by": "agent",
    "confidence": 0.97,
    "evidence": "队名、开赛时间(±10min)、收盘时间三项一致",
    "confirmed": false
  }
}
```

要求：

- `confirmed` 一律写 `false`。用户明确说"确认第 N 条"后才改真。
- `id` 规范：`<category>-<UTC日期>-<按字母序的参赛方缩写>`；
  更新既有条目时按 `id` 合并，不产生重复。
- `evidence` 写人能读懂的匹配依据，方便用户扫一眼就决定。
- 置信度低于 0.8 的组合不写入文件，在回复中单独列出并说明疑点。
- 写文件前先读旧文件，保留已 `confirmed` 的条目原样不动。

### 5. 汇报

用简短表格向用户汇报：每条提议的对阵/事件、时间、两平台市场、
置信度、疑点。结尾询问确认哪些条目。不要替用户做交易判断，
不要评价哪边"值得买"——本项目是只读陪看工具。

## 边界

- 任何平台请求失败：跳过该平台继续（单平台配对也有价值），
  在汇报中注明缺了哪个源。
- 不要为凑数强行配对：宁可"Kalshi 有、Polymarket 没找到"，
  也不要把语义不同的盘（让分/大小分/晋级）硬配成胜负盘。
- 不修改 watcher 代码或其他配置文件；不在 registry 里存 API key
  （这些 API 本就无需鉴权）。
