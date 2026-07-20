# VenueAdapter 契约（盘口源适配器）

状态：随 P1/P2 落地（2026-07）。英文版按路线图在 P3 架构定型后补齐。
背景与设计动机见 [multi-venue-roadmap-prd.zh-CN.md](multi-venue-roadmap-prd.zh-CN.md) 第 4 节。

模块：`tools/stackchan_venues.py`（仅标准库，HTTP 通过构造参数注入，
调用方负责重试与退避；测试注入 stub，不 patch urllib）。

## 归一化报价模型

所有平台的报价统一换算成 `VenueQuote`，聚合层不见任何平台原生单位：

| 字段 | 类型 | 约定 |
| --- | --- | --- |
| `venue` | str | `"kalshi"` / `"polymarket"` |
| `market_id` | str | 平台原生市场标识（Kalshi ticker / Gamma market id） |
| `outcome` | str | 规范化结果名，由调用方给定（如 `"left"`/`"right"` 或注册表结果名） |
| `prob_mid` | float\|None | 0.0–1.0。有订单簿取 bid/ask 中值；否则退回 last price；已结算取结算值 |
| `bid` / `ask` | float\|None | 0.0–1.0 概率空间 |
| `volume_usd` | float\|None | 美元口径。Kalshi 合约以 $1 结算，24h 合约量按 1:1 近似 |
| `liquidity_usd` | float\|None | 美元口径；未知填 None |
| `status` | str | `open` / `paused` / `closed` / `settled`（平台原生状态在适配器内收敛） |
| `close_time` | datetime\|None | UTC |
| `fetched_at` | datetime | UTC，取样时间 |

单位换算责任在适配器内部：Kalshi 报价是 dollars-per-contract（0–1），
Polymarket 是 0–1 份额价格、量与流动性为 USDC 数值字段。

## 适配器接口

```python
class VenueAdapter(Protocol):
    venue: str
    def discover(self, category: str, days: int) -> list[dict]: ...
    def quotes(self, market_refs: Sequence[Any]) -> list[VenueQuote]: ...
    def metadata(self, market_ref: Any) -> VenueMarketMeta | None: ...
```

- `discover(category, days)`：列出开放事件（含标题、收盘时间、市场列表），
  给配对助手和未来的发现流程用；watcher 运行时不调用。
- `quotes(market_refs)`：按平台原生引用批量取归一化报价。
- `metadata(market_ref)`：标题、结果列表、状态、收盘时间，启动校验用。

### KalshiVenueAdapter

- `market_ref` 是 market ticker 字符串。
- 额外提供 `raw_markets(tickers)`：保留给 watcher 的
  `fetch_markets`（`MarketSnapshot` 解析路径原样不动，P1 重构零行为变化）。
- 状态收敛：`active→open`；`finalized/settled/determined` 或带
  `result` → `settled`；`closed/inactive→closed`。

### PolymarketVenueAdapter

- `market_ref` 是 `PolymarketMarketRef(market_id, outcomes)`：
  `outcomes` 把规范化结果名映射到 Gamma `outcomes` 数组里的原文标签
  （如 `{"left": "Los Angeles Dodgers"}`）；留空则按原生标签全量报价。
- Gamma 的 `outcomes` / `outcomePrices` / `clobTokenIds` 是 JSON 编码的
  字符串，适配器负责解码。
- `bestBid`/`bestAsk` 描述第一个 outcome 的订单簿；二元盘的另一侧按
  `1 - ask, 1 - bid` 镜像，多路盘其余 outcome 不给 bid/ask。
- 速率约算 60 req/min：单场轮询预算充裕，发现扫描要注意批量与缓存。

## 聚合函数

- `aggregate_probability(quotes)`：同一结果的跨平台聚合概率。
  规则：已结算报价直接胜出 → 只留 `open` 报价 → 有更紧订单簿时剔除
  spread 超过 `AGGREGATION_SPREAD_CAP`（0.15）的报价 → 所有幸存报价都带
  流动性时做 liquidity 加权 mid，否则等权平均（避免已知/未知深度混权
  造成隐性偏置）。单源自然退化为单源值。
- `max_divergence(quotes, threshold=0.08)`：同一结果跨平台最大分歧，
  超过阈值（默认 8 分）返回 `VenueDivergence`，供解说层做"市场分歧"
  信息性播报。
- `same_direction_jump(delta_a, delta_b, min_abs)`：多源确认信号——
  两个平台同向、各自超过阈值的同时跳动，是 goal signal 的高置信升级。

## watcher 集成点（P2/P3 现状）

- `probability_bar.polymarket`（配置）把概率条的左右两路映射到一个
  Gamma market；watcher 每 `polymarket.poll_seconds`（默认 30s，下限
  15s）拉一次报价，概率条显示聚合值。
- Polymarket 拉取失败时退化为 Kalshi 单源：界面不报错、仅记日志，
  报价清空直到下次成功。
- 分歧 ≥8 分产出 `venue_divergence` 信息性提醒（10 分钟冷却）；
  Kalshi goal signal 在 90 秒窗口内被 Polymarket 同向跳动佐证时，
  升级为"双平台确认"话术与更高优先级。
- 配对注册表（`config/pairing_registry.json`）由 market-pairing skill
  离线提议、人工确认；watchlist 用 `active_canonical_event` 指向一条
  `confirmed: true` 的条目后，watcher 在 `load_config` 内派生全部盘口
  配置（两个市场、概率条、Polymarket 映射、开赛时间；足球品类附带
  ESPN 解说接线）。启动校验通过 `metadata()` 对照两个平台：Kalshi 市场
  缺失/已结算仅告警，Polymarket 对不上则丢映射降级单源；未确认条目
  直接拒绝，热重载遇到坏配置保留旧配置继续运行。
- 运行时零 LLM 依赖：agent 只在开发机上提议配对，比赛期间不在任何路径。
