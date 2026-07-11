# Stack-chan 三档播报语气 PRD

- 目标版本：Matchday MOD 1.4.0；个性化视角与球员目录为后续 watcher-only 更新
- 状态：已实现
- 范围：watcher 文案生成、支持/持仓视角、全局球员目录、watcher 本地设置页、
  设备 `/setup` 设置转发

## 目标

让用户在不更换 TTS 音色、语速或比赛数据源的前提下，选择三种稳定且可预测的
播报方式。三档共享同一份结构化事实，只改变文案组织和语气。

## 产品要求

### 共同事实

ESPN 事件先解析为结构化事实，再交给模板。可用字段包括比赛时间、事件类型、球队、
主要球员、必要参与者、事件结果与当前比分。点球、红黄牌和换人必须保留对应的主罚、
门将、领牌、上场与下场球员等必要参与者。

任何语气都不得把“疑似进球”“等待文字直播确认”等不确定事件写成已经确认。缺失、
含糊或无法可靠解析的细节直接忽略，不推断、不补写。

### 三档模板

- `casual`（朋友陪看）：口语、有适量网感，但球员、球队、结果和比分完整。
- `balanced`（自然播报）：清楚、自然，并作为旧配置的兼容默认值。
- `professional`（专业解说）：先播核心事实，再补充 ESPN 明确提供且能可靠解析的
  助攻或传中、射门方式、身体部位、射门区域、球门方向、门将扑救、定位球位置、
  换人或伤情原因。

专业档不能直接朗读英文文字直播原文。气泡在三档中都保持精炼，原则上为“时间 +
球员/球队 + 事件 + 比分”；详细信息主要由专业档语音承担。

### 支持队与持仓视角

`favorite_team` 表达用户的情感支持，`position_team` 表达用户手动声明的赛前持仓；
两者可以为空、相同或相反，watcher 不访问任何账户。文案先根据事件所属球队计算
支持方影响，再只对重大事件计算持仓影响：

- 普通攻防只使用支持队视角，避免每次射门、扑救或角球都机械谈仓位。
- 进球、点球、红牌和赛果等重大 ESPN 事件说明持仓利好或利空。
- 支持与持仓同向时合并表达；方向冲突时分别保留情感反应与仓位结果。
- Kalshi 盘口变化与疑似进球只有在市场已确认对应 `position_team`，且配置为
  `tracks_position` 时才使用持仓措辞；不确定事件必须保留“如果属实”和等待确认。

Match Setup 有持仓时启用该球队对应的市场，并设置 `tracks_position`；无持仓时
默认启用比赛双方列表中的第一个市场，但不把它描述为用户持仓。

### 全局球员目录

`config/espn_player_catalog.json` 以 `espn:<athlete_id>` 为稳定主键。每条记录可包含
ESPN 名称别名、正式中英文名、`featured` 标记、经人工核实的 casual 昵称，以及
可选进球口号。

- `balanced`、`professional` 与三档气泡只使用正式名。
- casual 语音可使用已核实昵称；没有昵称时仍使用正式名。
- 旧配置的 `espn.player_names` 与 `espn.star_chants` 作为逐场覆盖层继续生效，并
  优先于全局目录。
- 未知球员回退到 ESPN 原名；不得自动音译、猜测球星身份或编造昵称。
- roster 首次出现或变化时，watcher 日志输出正式名覆盖数、球星数与原名回退名单；
  覆盖不足不阻断事件播报。

首批中文名与昵称依据公开中文体育媒体及球员相关报道人工核实，包括：
[央视网：亚马尔](https://tv.cctv.com/2025/07/10/VIDE6Snrysk8wmjjqdZaIhGA250710.shtml)、
[PP 体育：德布劳内](https://www.ppsport.com/article/news/743879.html)、
[界面新闻：凯恩](https://www.jiemian.com/article/2238945.html)、
[Sky Sports：萨卡 “Little Chilli”](https://www.skysports.com/watch/video/sports/12340539/why-is-saka-called-little-chilli)、
[新浪：哈兰德](https://k.sina.com.cn/article_7879995911_1d5af320706801ykoi.html)，以及
[PP 体育：拉什福德“拉师傅”](https://www.ppsport.com/premierleague)、
[澎湃新闻：阿尔瓦雷斯“小蜘蛛”](https://www.thepaper.cn/newsDetail_forward_21159283)、
[澎湃新闻：姆巴佩“姆总”](https://www.thepaper.cn/newsDetail_forward_21217626)、
新华网的[中文阵容](https://app.xinhuanet.com/news/article.html?articleId=20260525a112ddc1a67a498f889ad189e3d7f573)和
[英文阵容](https://www.xinhuanet.com/sports/20260522/9ac26eb3e77a497582e666931638acbe/c.html)，以及
[阿根廷与瑞士赛事中文名](https://www.xinhuanet.com/sports/20260708/3529b2c9a4f148798a2bdf2e7087edeb/c.html)。
目录中的昵称仍须逐条人工判断是否适合口播；来源存在正式名并不等于认可任意昵称。

### 覆盖范围

语气覆盖 ESPN 比赛事件、比赛阶段和赛果，以及 Kalshi 盘口突变、价格跳动和疑似
进球提醒。语气仅改变文案；音效、庆祝动作、表情、灯效、优先级、提醒开关和轮询
策略保持不变。

## 设置与接口

- 配置键：`espn.commentary_style`。
- 有效值：`casual`、`balanced`、`professional`；缺失时为 `balanced`。
- watcher：`POST /api/setup/style`，请求体
  `{"commentary_style":"casual|balanced|professional"}`。
- 设备 MOD：`POST /api/match-setup/style`，通过现有 pending/ack 链路转发。
- watcher 本地设置页与设备 `/setup` 页都显示三段式选择器和当前生效值。

这是全局持久偏好。比赛中切换后，新生成的文案立即使用新语气；ESPN 已读事件、
盘口基线、既有告警队列和轮询状态不被清空或重建。

## 架构与发布边界

确定性解析与模板都位于 watcher，不调用大模型或联网改写服务。设备 MOD 只增加
设置、持久化和配置转发；官方 Stack-chan host firmware 与 TTS 模块不做改动。

升级到 1.4.0 时更新 watcher 并安装新版 Matchday MOD 即可，无需重新烧录 host。
本轮支持/持仓文案、市场选择与球员目录只修改 watcher；已有 1.5.0 Mod 无需重新
安装，也无需刷写 host。部署时必须同步 watcher 代码和全局球员目录，并重启
Python watcher 进程；仅在手机端切换语气仍是即时热更新，不重置比赛状态。

## 验收标准

- 同一进球在三档中都包含时间、进球球员、球队、事件结果和当前比分。
- 点球、红牌、换人、扑救、阶段变化与赛果通过相同的核心事实一致性检查。
- 专业档能使用 ESPN 样本中的助攻/传中、射门区域、身体部位、方向和门将信息，
  且语音比气泡更详细。
- 无法可靠解析的英文细节不会产生猜测或错误中文术语。
- 三档 Kalshi 疑似进球文案都明确保留不确定性和等待确认提示。
- 普通攻防只体现支持队；重大事件同时验证支持/持仓同向、冲突、仅支持、仅持仓和
  双方均未选择的文案。
- Match Setup 有持仓时只启用对应市场；无持仓时启用第一市场且不标记为持仓。
- 正式名、casual 昵称、旧配置覆盖、未知球员原名回退和 roster 覆盖日志均通过测试。
- 只切换语气不会重播旧 ESPN 事件，也不会重置盘口状态、队列或轮询状态。
- watcher、手机设置服务、设备 pending/ack 链路和 MOD 页面通过完整测试。

## 非目标

- 不切换 TTS 说话人、音色或语速。
- 不增加 ESPN 之外的比赛数据源。
- 不使用大模型生成或在线改写文案。
- 不修改官方 host firmware 或 TTS 模块。
