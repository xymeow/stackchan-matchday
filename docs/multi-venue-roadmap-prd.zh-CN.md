# Stack-chan Matchday 多品类·多盘口演进路线 PRD

- 更新日期：2026-07-15
- 状态：规划中
- 范围：世界杯结束后的架构演进——品类扩展（MLB、网球、英超、NBA、NFL、
  政治、选秀）、多盘口源聚合（Kalshi + Polymarket）、AI 辅助配对发现

## 1. 背景

当前系统把"足球世界杯"和"Kalshi 晋级盘"两个假设写进了核心路径：
`espn.league` 默认 `fifa.world`，赛程发现绑定 `KXWCADVANCE` series，
概率条假设"双方归一化"两路结果。世界杯 7 月 19 日结束后，这套管线
将失去输入。

同时，外部条件已经变化：

- Kalshi 已覆盖约 17 个运动（NFL、NBA、MLB、网球、英超、欧冠等），
  提供实时 in-play 盘口，平台按 categories → series → events → markets
  四层组织，`/series` 与 `/events` 可用于程序化发现。
- Polymarket Gamma API（`gamma-api.polymarket.com`）只读、无需鉴权，
  约 60 req/min，覆盖体育、政治、娱乐等品类。

## 2. 目标

- 品类可插拔：新增一个运动或非体育品类，只需实现一个品类适配器，
  不改核心 watcher。
- 盘口源可插拔：新增一个盘口平台，只需实现一个盘口源适配器；
  概率条与提醒消费聚合后的归一化报价。
- 自动发现：扫描盘口平台的开放事件，按热度排序推荐给用户，
  而不是依赖单一 series 的手工配置。
- 配对可信：跨平台实体匹配由 AI 配对助手提议、人工确认、
  注册表持久化；watcher 运行时不依赖 LLM。
- 现有足球体验（解说三档、剧透保护、球星应援、手机设置）全部保留。

## 3. 非目标（继承并延续）

- 不自动下单、不读取任何平台账户、不提供投注建议或套利策略。
- 不把任何 API key 写入设备固件；Kalshi 与 Polymarket 读取路径
  均不需要鉴权。
- 不做公网服务；所有组件保持本地优先、可信局域网内运行。
- 呈现跨平台报价分歧属于信息展示，不得使用"该买哪边"式话术。

## 4. 架构：两条正交扩展轴

```text
盘口源适配器 (VenueAdapter)          品类适配器 (CategoryAdapter)
  Kalshi ──┐                           足球 (ESPN soccer)
  Polymarket ─┤                        MLB / 网球 (ESPN)
  未来平台 ──┘                         政治 / 选秀 (无逐字直播)
        │                                    │
        ▼                                    ▼
   归一化报价 ──► 聚合器 ──► 配对注册表 ◄── 事件解析/解说
                     │
                     ▼
              watcher 核心（队列、优先级、显示、语音、动作）
```

品类适配器回答"现实世界发生了什么"；盘口源适配器回答"市场认为
概率是多少"。两轴在配对注册表处汇合：注册表声明"现实中的同一件事"
分别对应哪个平台的哪个 market、哪个赛事源的哪个 event。

### 4.1 盘口源适配器（VenueAdapter）

接口职责：

- `discover(category, window)` — 列出开放事件（含成交量、临近收盘、
  近期波动等热度信号）。
- `quotes(market_refs)` — 返回归一化报价。
- `metadata(market_ref)` — 标题、结果列表、收盘时间、结算状态。

归一化报价模型（所有平台统一）：

```python
@dataclass
class VenueQuote:
    venue: str              # "kalshi" | "polymarket"
    market_id: str
    outcome: str            # 规范化结果名，对应注册表 outcomes
    prob_mid: float         # 0.0–1.0
    bid: float | None
    ask: float | None
    volume_usd: float | None
    liquidity_usd: float | None
    status: str             # open | paused | closed | settled
    close_time: datetime | None
    fetched_at: datetime
```

单位注意：Kalshi 报价为 cents、量为合约数；Polymarket 为 0–1 份额、
量为 USDC。适配器内部完成换算，聚合层只见统一模型。

### 4.2 聚合器

- 主输出：每个规范化结果一个聚合概率。首版采用
  **liquidity 加权 mid**（盘口越紧、深度越好权重越高），
  而非累计成交量加权——累计量反映历史热度，不反映当下报价质量。
- 分歧信号：任意两平台对同一结果的 mid 差超过阈值（建议 8 分）时，
  产出一条"市场分歧"事实，交由解说层用信息性话术播报。
- 多源确认：现有 `goal_signal`（盘口跳动=疑似事件）升级为多源版本——
  单一平台跳动维持现有"等待确认"话术；**两个平台同向同时跳动**
  提升优先级与置信度。该信号与品类无关，是新品类在没有逐字直播
  解析器之前的默认反应层。
- 降级：单一平台不可用时聚合器退化为单源，界面不报错、
  仅在日志记录。

### 4.3 配对注册表（pairing registry）

新配置文件 `config/pairing_registry.json`，watcher 只消费
`confirmed: true` 的条目：

```json
{
  "canonical_events": [
    {
      "id": "mlb-2026-07-21-NYY-BOS",
      "category": "mlb",
      "label": { "zh": "扬基 vs 红袜", "en": "Yankees vs Red Sox" },
      "starts_at": "2026-07-21T23:10:00+00:00",
      "outcomes": ["NYY", "BOS"],
      "event_source": {
        "provider": "espn",
        "league": "baseball/mlb",
        "event_id": "401234567"
      },
      "venue_markets": [
        {
          "venue": "kalshi",
          "event_ticker": "KXMLBGAME-26JUL21NYYBOS",
          "outcome_map": {
            "NYY": "KXMLBGAME-26JUL21NYYBOS-NYY",
            "BOS": "KXMLBGAME-26JUL21NYYBOS-BOS"
          }
        },
        {
          "venue": "polymarket",
          "event_id": "yankees-red-sox-2026-07-21",
          "outcome_map": { "NYY": "<token_id>", "BOS": "<token_id>" }
        }
      ],
      "pairing": {
        "proposed_by": "agent",
        "confidence": 0.97,
        "evidence": "队名、开赛时间(±1h)、收盘时间三项一致",
        "confirmed": true
      }
    }
  ]
}
```

设计原则：

- `event_source` 可为空——纯盘口（standalone）事件是一等公民，
  政治、选秀等无逐字直播的品类默认走此路径。
- `outcomes` 是数组而非左右两方——二元只是 N=2 的特例，
  为大选、选秀状元签等多路盘预留。
- 配对写入与确认分离：AI 助手（见第 5 节）只提议，
  用户在手机设置页或对话中确认后 `confirmed` 才置真。
  该模式延续现有"watcher 验证 ESPN/Kalshi 球队一致后才应用"的惯例。

### 4.4 品类适配器（CategoryAdapter）

接口职责：

- `discover()` — 列出该品类的可看事件（体育走 ESPN scoreboard，
  非体育品类可缺省，改由盘口发现驱动）。
- `parse_events(payload)` — 把赛事源原始数据解析为结构化事实
  （沿用解说 PRD 的"共同事实"契约）。
- `reaction_vocabulary()` — 事件类型 → 优先级、语音模板、
  灯效/动作的映射。
- `display_hints()` — 概率条模式、旗帜/头像资源等。

足球是第一个实现（从现有代码提取，行为不变，由既有测试套件守护）。
每个新品类的实际工作量集中在 `parse_events`：ESPN 各运动 schema
互不相同且无文档，必须配套录制回放（复用
`stackchan_match_replay.py` 模式）与合约测试。

### 4.5 显示模式

| 模式 | 适用 | 说明 |
| --- | --- | --- |
| `two_way_bar` | 足球、MLB、网球等二元盘 | 现有归一化概率条，不变 |
| `top_n` | 大选、选秀、夺冠赛道等多路盘 | 显示前 2–3 名 + "其他"，可轮播 |
| `single_gauge` | 标量/单一二元盘 | 现有 `binary_complement` 的推广 |

多路模式在 P5 之前不实现，但归一化报价与注册表从 P1 起就按
N 路结果建模。

## 5. AI 配对助手（agent skill）

跨平台实体匹配（Kalshi 结构化 ticker ↔ Polymarket 自由文本标题 ↔
ESPN event）是全系统最易错的环节，也最不适合写成硬编码规则。
方案：做成仓库内 agent skill（`.claude/skills/market-pairing/`），
由 AI agent 在开发机上按需执行，产出注册表**提议**。

流程契约：

1. 用户给出品类与日期窗口（如"帮我配下周的 MLB"）。
2. agent 运行 `scripts/fetch_pairing_candidates.py` 分别拉取
   Kalshi `/events`、Polymarket Gamma `/events`、ESPN scoreboard
   的精简候选列表（脚本负责裁剪字段，避免原始响应浪费上下文）。
3. agent 做模糊匹配：队名/人名规范化、开赛时间容差（±2 小时）、
   收盘时间与赛期一致性、结果数量一致性。
4. 每条提议附 `confidence` 与 `evidence`，写入注册表并保持
   `confirmed: false`；低置信候选列出而不写入。
5. 用户确认后置 `confirmed: true`（手工或让 agent 代改）。

安全边界：

- skill 只写 `config/pairing_registry.json`，不碰 watcher 其他配置。
- watcher 对 `confirmed: true` 条目仍做启动校验（结果名与
  outcome_map 键一致、市场存在且未结算），校验失败降级为忽略该
  venue 并告警，而不是崩溃。
- 运行时零 LLM 依赖：agent 不在比赛期间参与任何路径。

## 6. 分阶段路线图

| 阶段 | 范围 | 验收 | 明确不做 |
| --- | --- | --- | --- |
| P0 续命 | 现有足球管线指向英超/欧冠（config-only：`league`、`series_ticker`） | 8 月英超开赛可直接陪看 | 不改代码结构 |
| P1 盘口源重构 | 抽出 VenueAdapter + VenueQuote，Kalshi 为首个实现；纯重构 | 全部既有测试通过，行为零变化 | 不加新平台 |
| P2 Polymarket 只读 | Polymarket adapter，仅接 standalone 模式 | 任选一个两平台共有盘，设备显示聚合概率与分歧 | 不做 ESPN 配对 |
| P3 注册表 + 聚合 | 配对注册表接入主流程，概率条消费聚合概率；market-pairing skill 首版 | 一场真实比赛以双平台聚合完整陪看 | 不做自动确认 |
| P4 首个新品类 | MLB（或网球）CategoryAdapter：ESPN 解析、回放录制、反应词汇 | 完整陪看一场 MLB，含解说三档 | 不做多路显示 |
| P5 多路显示 | `top_n` 模式 + 多路聚合展示 | 一个真实多路盘（如选秀/夺冠盘）常驻显示 | 不做开票夜事件源 |
| P6 非体育品类 | 政治/选秀品类适配器：以定时发布、盘口跳动为"事件" | 大选或选秀夜完整陪看 | 视届时需求定 |

日历对齐：世界杯 7/19 结束 → P1/P2 为纯软件活，正好填 7 月底至
8 月初空窗 → 英超 8 月中开赛承接 P0/P3 实战 → MLB/网球整个夏天
可用作 P4 练手 → NBA/NFL 赛季（9–10 月）时适配器接口已定型，
新增只剩解析器与词汇表 → 2026 美国中期选举（11 月）承接 P5/P6。

## 7. 数据源与限制

- **Kalshi**：public REST 无需鉴权；`/series`、`/events` 支持按
  category 发现；in-play 盘为实时概率条首选。沿用现有自适应轮询
  与退避策略。
- **Polymarket**：Gamma API 只读无鉴权，约 60 req/min——单场轮询
  足够，但发现扫描需注意批量与缓存；报价 0–1、量为 USDC。
- **ESPN**：各运动端点均无文档、随时可变；每个品类适配器必须
  配录制回放与合约测试，解析失败时降级为纯盘口模式。
- **显示**：CoreS3 屏幕小，`top_n` 最多前 3 名；旗帜资源模式需要
  推广为"头像/徽标资源包"（复用 flag pack 生成器）。

## 8. 风险与对策

- 配对错误导致播错比赛 → 人工确认门槛 + watcher 启动校验 +
  聚合概率与 ESPN 比分严重矛盾时自动静默盘口提醒。
- 两平台结果命名不一致（如 "Yankees" vs "New York Yankees"）→
  规范化结果名只存在于注册表，平台原始名只出现在 outcome_map。
- 单平台流动性枯竭拖歪聚合 → liquidity 加权天然抑制；
  spread 超过阈值的报价直接剔除。
- ESPN schema 漂移 → 回放测试锁定已知格式，解析失败降级而非崩溃。
- 品类适配器越来越多导致配置膨胀 → 品类默认值内置于适配器，
  `kalshi_watchlist.json` 只保留用户偏好与当前事件。

## 9. 文档同步

本文档进入 `docs/` 后，按 development.md 的同步清单维护：
阶段完成时更新对应释出说明；P1 落地时把 VenueAdapter 契约固化为
`docs/venue-adapter-api.md`；英文版在架构定型（P3）后补齐。
