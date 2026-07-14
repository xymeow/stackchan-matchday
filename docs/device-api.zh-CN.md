# 设备 API

[English](device-api.md) | [简体中文](device-api.zh-CN.md)

Matchday Mod 在 Stack-chan 的 TCP `80` 上提供无认证 HTTP API。它用于同一可信
局域网内的 watcher、手机设置页和手工诊断；不要将端口转发到互联网。

以下示例假设：

```sh
export STACKCHAN_HOST=stackchan.local
```

## 健康与状态

| 方法与路径 | 用途 |
| --- | --- |
| `GET /health` | 最小存活检查 |
| `GET /api/status` | Mod 版本、概率条、TTS、电源、网络、静音和设置触发计数 |
| `GET /api/help` | `POST /api/command` 支持的纯文本命令 |

```sh
curl "http://$STACKCHAN_HOST/health"
curl "http://$STACKCHAN_HOST/api/status"
curl "http://$STACKCHAN_HOST/api/help"
```

## 纯文本命令

向 `POST /api/command` 发送单条纯文本命令：

```sh
curl --request POST --data-binary "say 比赛日准备好了" \
  "http://$STACKCHAN_HOST/api/command"
```

常用命令：

```text
pkbar es 62 AA151B be 38 EF3340
balloon temp 8000 西班牙进球了！
voice favorite-goal 7号球员进球啦
celebrate goal 170 21 27
celebrate say 170 21 27 进球了！
celebrate result win 170 21 27 比赛结束
setup show http://stackchan.local/setup
say 你好
mute on 60
mute off
mute status
face happy
look 8 -2
idle look on
light flash 0 85 164
```

参数格式和当前可用命令以设备返回的 `GET /api/help` 为准。发送动态语音前，可先用
`tts host <watcher-ip>:8787` 设置局域网 TTS，再用 `tts status` 检查连接。

## JSON 控制

`POST /api/control` 接受 JSON action，供控制面板和结构化客户端使用。设备能力可能
随 Mod 版本变化；客户端应检查 HTTP 状态并在需要时读取 `/api/status`，不要假定
命令已执行。

```sh
curl --request POST \
  --header 'Content-Type: application/json' \
  --data '{"action":"mute","enabled":true,"minutes":60}' \
  "http://$STACKCHAN_HOST/api/control"
```

## Match Setup

设备托管 `GET /setup` 手机页面。watcher 使用以下接口完成 options/pending/ack
中继：

| 路径 | 角色 |
| --- | --- |
| `/api/match-setup` | Match Setup 主资源 |
| `/api/match-setup/options` | watcher 发布可选比赛和市场 |
| `/api/match-setup/apply` | 手机提交选择 |
| `/api/match-setup/pending` | watcher 读取待处理选择 |
| `/api/match-setup/ack` | watcher 确认或拒绝应用结果 |
| `/api/match-setup/language` | 更新界面语言 |
| `/api/match-setup/style` | 更新播报语气 |
| `/api/match-setup/spoiler` | 更新防剧透模式 |

这是一个异步确认流程：设备收到手机选择不代表 watcher 已采用。客户端应显示待处理
状态，直到 watcher 返回 ack；不要在提交后自行假定配置成功。

### 即时切换语气

设备接收 `casual`、`balanced` 或 `professional` 之一，例如：

```http
POST /api/match-setup/style
Content-Type: application/json

{"commentary_style":"professional"}
```

它通过现有 pending/ack 流程转发。watcher 本机设置服务的对应接口为：

```http
POST /api/setup/style
Content-Type: application/json

{"commentary_style":"professional"}
```

`GET /api/setup/status` 返回 watcher 当前生效值。切换只影响新生成文案，不应重置
ESPN 已读事件、盘口基线、告警队列或轮询状态。

### 即时切换防剧透模式

设备接收严格的 JSON 布尔值：

```http
POST /api/match-setup/spoiler
Content-Type: application/json

{"spoiler_free_mode":true}
```

它通过 pending/ack 流程转发。watcher 本机设置服务的对应接口为：

```http
POST /api/setup/spoiler
Content-Type: application/json

{"spoiler_free_mode":true}
```

`GET /api/setup/status` 返回当前生效的 `spoiler_free_mode`。只切换该偏好不会重载
比赛或重置 ESPN 历史、盘口基线与轮询；开启后丢弃队列中的 Kalshi 提醒，已确认的
ESPN 事件照常播报，概率条和 ticker 继续静默更新。

## 调用约定

- 所有接口仅用于可信局域网；设备 API 无认证并开放 CORS。
- 文本命令使用 UTF-8，请保留中文正文，不要先转成英文再交给设备。
- watcher 的 HTTP 投递应检查失败并退避，不要无间隔重试；设备资源有限。
- 长时间 TTS 播放期间不要用高频状态轮询压迫设备。
- 需要设置页和 pending/ack 时必须使用 HTTP；串口 transport 不提供这条链路。
- 修改接口时同步更新 Mod、watcher 客户端、测试和本文。
