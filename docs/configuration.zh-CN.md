# 配置与播报

[English](configuration.md) | [简体中文](configuration.zh-CN.md)

本文说明 watcher 的持久配置、手机 Match Setup、语言与三档播报、支持/持仓视角、
球员目录和独立市场模式。配置示例位于
`config/kalshi_watchlist.example.json`；请把本机修改写入未提交的
`config/kalshi_watchlist.json`。

## 配置入口

| 入口 | 用途 |
| --- | --- |
| 设备 `http://<stackchan>/setup` | 日常选择比赛、语言、支持队、持仓和语气；推荐入口 |
| watcher `http://127.0.0.1:8788/setup` | watcher 电脑上的本机管理后备页 |
| `config/kalshi_watchlist.json` | 轮询、提醒开关、市场、TTS 和高级 ESPN 配置 |

手机设置流程使用 pending/ack：设备先保存待处理选择，watcher 校验 ESPN 与 Kalshi
双方匹配，原子更新 JSON、热重载，再确认设备。只切换语气时不会重置 ESPN 已读事件、
盘口基线、告警队列或轮询状态。

手机设置、设备状态检测和 options/pending/ack 中继都依赖 HTTP。串口模式只适合直接
命令和控制；使用时还需安装 `pyserial` 并设置 `stackchan_serial_port`。

## 最小 watcher 配置

```json
{
  "stackchan_host": "stackchan.local",
  "stackchan_transport": "http",
  "language": "zh",
  "setup_server": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8788,
    "kalshi_series_ticker": "KXWCADVANCE",
    "lookahead_days": 10,
    "refresh_seconds": 900
  },
  "espn": {
    "enabled": false,
    "commentary_style": "balanced"
  },
  "markets": [{
    "ticker": "KXEXAMPLE-TEAM",
    "label": {"zh": "示例市场", "en": "Example market"},
    "side_i_care": "yes"
  }]
}
```

watcher 至少需要一个市场；这里的 `KXEXAMPLE-...` ticker 不是真实市场。通过手机
选择比赛后，watcher 会写入
匹配结果；也可以用 discover 命令查找开放市场：

```sh
python3 tools/stackchan_kalshi_watch.py discover --query 关键词
```

## 语言与本地化文本

顶层 `language` 接受 `zh` 或 `en`。设置页会立即切换设备界面文案并持久化；应用
一场比赛后，watcher 生成的语音和气泡也使用该语言。

面向用户的配置文本既可以是旧式字符串，也可以是中英对象：

```json
{
  "language": "en",
  "mac_voice": {"zh": "Tingting", "en": "Samantha"},
  "espn": {
    "label": {"zh": "法国 vs 摩洛哥", "en": "France vs Morocco"},
    "team_names": {
      "France": {"zh": "法国", "en": "France"}
    }
  },
  "markets": [{
    "ticker": "KXEXAMPLE-FRA",
    "label": {"zh": "法国晋级", "en": "France to advance"}
  }]
}
```

## 三档播报语气

`espn.commentary_style` 是全局持久偏好，只接受 `casual`、`balanced` 或
`professional`。旧配置缺少该字段时默认 `balanced`。

| 配置值 | 语音 | 设备气泡 |
| --- | --- | --- |
| `casual` | 朋友陪看的口语表达，但完整保留核心事实 | 精炼事件摘要 |
| `balanced` | 清楚、自然，兼容此前播报 | 精炼事件摘要 |
| `professional` | 先播核心事实，再补充可靠解析出的 ESPN 细节 | 仍精炼，详细信息交给语音 |

三档共用解析后的结构化事实，必须保留比赛时间、事件类型、球队、必要球员、事件结果
和当前比分；点球、红黄牌和换人也保留必要参与者。“疑似进球”“等待文字直播确认”
等不确定性在任何语气下都不会省略。

专业档只使用 ESPN 明确提供且 watcher 能可靠解析的助攻或传中、射门方式和身体
部位、场上或球门位置、门将扑救、定位球位置、换人或伤情原因。它不会朗读英文原文，
也不会猜测缺失或含糊的细节。

三档气泡原则上保持“时间 + 球员/球队 + 事件 + 比分”，只允许标点和少量措辞差异。
语气只改变文案，不改变 TTS 音色和语速、音效、动作、表情、灯效、优先级或提醒
开关。比赛中切换立即作用于新提醒，但不会重播旧事件。

## 支持队与持仓队

- `espn.favorite_team` 是支持队，决定普通射门、扑救、角球等日常陪看视角；可中立。
- `espn.position_team` 是手动填写的持仓偏好；可选择“没买”。系统不读取真实账户。
- 进球、点球、红牌和赛果等重大事件才补充持仓利好或利空。
- 支持与持仓同向时合并成自然反应；方向冲突时分别说明支持方向和仓位影响。
- Match Setup 有持仓时启用该队对应市场；没买时默认启用第一个市场，但不会把它
  描述成持仓。
- 只有与持仓队匹配且标记为 `tracks_position` 的 Kalshi 市场，盘口变化和疑似进球
  才使用持仓措辞；疑似事件仍保留“如果属实”和等待确认。

## 球员名字、昵称和进球口号

watcher 先查全局 `config/espn_player_catalog.json`。目录以稳定的 ESPN athlete ID
（如 `espn:362150`）为主键，保存正式中英文名、经人工核实的 casual 昵称、球星
标记和可选进球口号。

- `balanced`、`professional` 和所有气泡始终使用正式名。
- 只有 `casual` 语音可以使用目录中的亲切昵称。
- 旧配置内的 `player_names` 和 `star_chants` 继续有效，并优先于全局目录。
- 自定义进球口号支持字符串或 `{ "zh": "...", "en": "..." }`。
- 未命中目录的球员回退 ESPN 原名，不音译、不猜昵称。

ESPN roster 首次出现或变化时，watcher 会记录目录命中数、球星数和原名回退名单。
维护者应在赛前根据这些日志补齐目录，并为昵称保留可靠来源；正式名来源不等于可以
推断昵称。

局部覆盖示例：

```json
{
  "espn": {
    "player_names": {
      "Kylian Mbappé": {"zh": "姆巴佩", "en": "Kylian Mbappe"}
    },
    "star_chants": {
      "Kylian Mbappé": {
        "zh": "{name}！{name}！打进去了！",
        "en": "{name}! {name}! He scores!"
      }
    }
  }
}
```

## 轮询、提醒和静默时段

顶层 `poll_seconds` 是基础间隔；`adaptive_polling` 可在远离开赛、赛前预热、临近
开赛和赛后使用不同的 Kalshi/ESPN 间隔。ESPN 比赛中的 `espn.poll_seconds` 可单独
设置。过短间隔会增加设备与上游压力，不保证上游数据更早出现。

常用提醒字段包括：

- `max_alerts_per_cycle`：单轮最多投递的提醒数。
- `startup_summary_on_watch` / `speak_startup_summary`：启动摘要及是否朗读。
- `display_refresh_seconds`：常驻显示刷新间隔。
- `quiet_hours`：免打扰时段。
- `alert_balloon_seconds`：普通提醒气泡时长。
- 各市场的 `alert_move_cents`、`speak_move_cents`、`min_seconds_between_alerts` 和
  `alerts_enabled`。

watcher 还可每天提醒扫码选比赛：

- `setup_server.daily_prompt_hour`：本地小时；`-1` 关闭。
- `setup_server.prompt_minutes_before`：开赛前提醒窗口。
- `setup_server.lookahead_days`：比赛发现范围。
- `quiet_hours`：抑制不合时段的主动提醒。

## 静音（老板键）

长按头顶触摸条约一秒可切换不限时静音。静音停止远程 TTS、提示音、原始 `tone`、
庆祝动作和警报闪灯；概率条、气泡、ticker 和手机设置页继续工作。不限时静音跨
重启保留，定时静音不跨重启。

可通过设备命令使用 `mute on [分钟]`、`mute off`、`mute status`，或在控制面板
选择定时静音。完整接口见[设备 API](device-api.zh-CN.md)。

## 独立市场模式

没有比赛时，可在设备设置页粘贴 Kalshi event URL 或 ticker。watcher 会选择该事件
中成交最活跃的最多四个市场，在底部 ticker 中显示；比赛专属旗帜概率条和 ESPN
播报暂时关闭。重新选择比赛后恢复比赛模式。
