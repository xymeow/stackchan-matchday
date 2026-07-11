# Stack-chan Matchday

[English](README.md) | [简体中文](README.zh-CN.md)

Stack-chan Matchday 是一个轻量的
[Stack-chan](https://github.com/stack-chan/stack-chan) Mod 与 Python
局域网 watcher，可把 CoreS3 机器人变成世界杯陪看搭子：屏幕持续显示双方在
Kalshi 晋级市场中的概率，跟随 ESPN 比分与文字直播，并通过语音、气泡、灯光和
安全幅度的头部动作做出反应；下一场看什么，可以直接用手机选择。

> [!IMPORTANT]
> 这是一个只读的比赛陪看工具。它不会交易、不会访问 Kalshi 账户，也不提供投注
> 建议。`position_team` 只是手动填写的偏好：watcher 会在重大 ESPN 事件、赛果以及
> 已确认对应持仓的 Kalshi 市场变化中说明利好或利空，但不会读取真实账户。Kalshi
> 数据来自其公开 REST API；ESPN 数据来自可公开访问但未正式文档化的接口，可能
> 变更，也可能落后于电视直播。

## 功能

- 常驻的双方概率条、球队旗帜与底部市场 ticker。
- 对进球、红黄牌、换人、险情、比赛状态和终场结果做出反应。
- 三档可即时切换的播报语气：朋友陪看的 `casual`、自然播报的 `balanced`，以及
  补充 ESPN 明确信息的专业解说 `professional`。
- 支持队与持仓队分开建模：普通攻防站在支持队视角，重大事件再自然补充持仓影响，
  两者方向相反时也会明确表达冲突。
- 由 Stack-chan 自己托管的手机设置页，可即时切换中英文。
- 双击头顶触摸条或短按 Power 键唤出设置二维码。
- 自动发现比赛、自适应轮询、配置热更新与免打扰时段。
- 可选局域网 TTS；没有 TTS 时，视觉反馈和提示音仍然可用。
- 没有比赛时，也可单独跟踪一个事件中最多四个活跃市场。

## 实机效果

<table>
  <tr>
    <td colspan="2" align="center">
      <img src="docs/images/photos/matchday-in-action.jpg" alt="Stack-chan 在笔记本电脑旁跟随足球比赛" width="900"><br>
      <sub>一起看球：Stack-chan 在屏幕旁跟随同一场比赛。</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="68%">
      <img src="docs/images/photos/device-probability-bar.jpg" alt="笔记本电脑旁的 Stack-chan，屏幕显示西班牙 92、另一侧 8 和中文盘口变化提示" width="560"><br>
      <sub>实时反应：92–8 概率与设备端盘口变化提示。</sub>
    </td>
    <td align="center" width="32%">
      <img src="docs/images/photos/phone-setup-zh.png" alt="Stack-chan 中文赛前设置页，显示语言、播报语气、等待 watcher 状态和未来比赛" width="200"><br>
      <sub>中文设置页：切换播报语言和语气，并等待 watcher 确认。</sub>
    </td>
  </tr>
</table>

<details>
<summary>🥚 彩蛋</summary>

我买🇧🇪了，猜猜明天我会不会上天台🤣

<sub>这里的“持仓”只由你手动选择；Stack-chan 不读取账户，也不会下单。</sub>

</details>

## 系统设计

![Stack-chan Matchday 系统设计](docs/images/system-design.png)

Kalshi 和 ESPN 只作为 Python watcher 的只读数据源。watcher 向 Matchday Mod
发送可选比赛、显示命令和设置确认。比赛事件先在 watcher 中解析成三档共享的
结构化事实，再按当前语气生成文案；Mod 只负责转发设置并执行显示、语音和动作命令，
不为此修改官方 host firmware 或 TTS 模块。手机访问的是 Stack-chan 自己的 `/setup`
页面：设备先保存待处理选择，watcher 再完成校验、原子更新本地 JSON 配置、热重载，
最后确认设备。播报时，Stack-chan 主动向可选局域网 TTS 服务请求
`/say?text=...`，并接收 24 kHz、单声道、16-bit PCM WAV。

手机、watcher 电脑、TTS 服务和 Stack-chan 必须处于同一个可信局域网。
watcher 电脑上的 `:8788/setup` 只是可选的本机管理后备页，不是扫码主流程。

## 仓库结构

- `mod/` — 设备端 Mod，由多个小型 JS 模块以及旗帜、二维码资源组成。
- `host/` — CoreS3 必需的分区补丁、可选 CJK 字体补丁和字体准备脚本。这些只是
  构建/资源层改动，不修改上游 runtime JS/C 源码。
- `tools/` — watcher、本地设置服务、macOS TTS 服务、比赛回放、串口辅助工具、
  素材生成器与测试。默认 HTTP 流程只用 Python 标准库；串口模式另需
  `pyserial`。
- `config/` — watcher 示例配置、旗帜包定义与全局 ESPN 球员目录。
- `docs/` — [三档播报语气 PRD](docs/commentary-styles-prd.md) 与各版本升级说明，
  包括双语的 [Matchday MOD 1.5.0 说明](docs/releases/1.5.0.md)与
  [1.4.0 说明](docs/releases/1.4.0.md)。

## 环境要求

- 基于 CoreS3、配备 16 MB Flash 的 Stack-chan，以及一根可传数据的 USB 线。
- 构建电脑上安装 Git、Python 3.10+、Node.js 20+（上游已测试 Node.js 22）、
  npm 和 `xz`。
- Moddable SDK 与 ESP-IDF；下文的上游 `xs-dev` 命令会负责安装并检查。
- 手机、watcher 电脑和 Stack-chan 位于同一个可信局域网。
- 只有生成设备专属二维码时才需要 `qrencode`。
- 仓库自带的 `say` TTS 服务仅支持 macOS；其他系统可以不启用语音，或自行提供
  兼容的 `/say` WAV 服务。

下列命令适用于 macOS/Linux shell。先设置两个绝对路径，后续安装始终沿用：

```sh
mkdir -p "$HOME/src"
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
export STACKCHAN_DIR="$HOME/src/stack-chan"
```

## 安装

如果已经在使用旧版 Matchday，要升级 1.4.0 三档播报，只需更新本 watcher 仓库并
重新安装 Matchday Mod；无需重新构建或烧录官方 host firmware，也无需更换 TTS
模块。详见 [1.4.0 版本说明](docs/releases/1.4.0.md)。

支持/持仓视角文案、按持仓选择市场和全局球员目录属于 watcher-only 更新：已有
1.5.0 Mod 可直接沿用。请同步更新 watcher 代码与
`config/espn_player_catalog.json`，然后重启 Python watcher 进程；无需刷写 Mod 或 host。

### 1. 克隆仓库并准备上游构建环境

```sh
git clone https://github.com/xymeow/stackchan-matchday.git "$MATCHDAY_DIR"
git clone https://github.com/stack-chan/stack-chan.git "$STACKCHAN_DIR"

cd "$STACKCHAN_DIR"
git switch --detach ded5ca94ef50411aec213b85a23d1afe72d4c29e

cd "$STACKCHAN_DIR/firmware"
npm ci
npm run setup -- --device=esp32
npm run doctor
```

这个固定 commit 是本仓库补丁测试过的基线。只有在 `npm run doctor` 把 `esp32`
列为 supported target 后，才继续构建。若 `xs-dev` 提示平台相关依赖，请查看上游
[环境配置说明](https://github.com/stack-chan/stack-chan/blob/dev/v1.0/firmware/docs/getting-started.md)。

### 2. 应用补丁并烧录一次 host

每个新的 CoreS3 host checkout 都必须应用分区补丁：

```sh
cd "$STACKCHAN_DIR"
git am "$MATCHDAY_DIR/host/patches/0001-Add-xs-mod-partition-for-M5StackChan-CoreS3.patch"
```

要显示中文标签和气泡，还需应用字体补丁并准备一份支持 CJK 的 TTF。纯英文安装
可以跳过下面两条命令，并把 watcher 的语言设为 `en`：

```sh
git am "$MATCHDAY_DIR/host/patches/0002-Add-optional-StackChanCN-24-GB2312-font-resource.patch"
python3 "$MATCHDAY_DIR/host/prepare_cjk_font.py" "$STACKCHAN_DIR"
```

构建并烧录 host：

```sh
cd "$STACKCHAN_DIR/firmware"
export PATH="$PWD/node_modules/.bin:$PATH"
mcconfig -d -m -p esp32:./platforms/m5stackchan_cores3 -t deploy \
  "$PWD/stackchan/manifest_m5stackchan_cores3.json"
```

字体选择与补丁细节见 [host/README.zh-CN.md](host/README.zh-CN.md)。

### 3. 先生成二维码，再构建并安装 Mod

二维码是编译进 Mod 的静态图片，运行时不会自动重画。请先为设备设置稳定的 DHCP
保留地址、IP 或可解析的 mDNS 名称，然后在安装 Mod 前生成二维码。UI 会读取 PNG
的实际尺寸；请让宽和高都不超过 168 px，以便标题和 URL 仍能完整显示：

```sh
export STACKCHAN_HOST=stackchan.local
qrencode -s 4 -m 1 -o "$MATCHDAY_DIR/mod/assets/setup/setup-qr.png" \
  "http://$STACKCHAN_HOST/setup"
file "$MATCHDAY_DIR/mod/assets/setup/setup-qr.png"
```

如果 `file` 显示任一边超过 168 px，请改用 `-s 3` 重新生成。然后构建并安装
Mod：

```sh
cd "$STACKCHAN_DIR/firmware"
npm run mod --target=esp32:./platforms/m5stackchan_cores3 -- -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
```

CoreS3 必须使用 `-f rgb565be`，否则旗帜颜色会发生字节序错位。

`npm run mod` 走 xsbug 调试协议安装，需要 xsbug 监听端口，且设备忙时可能在写入
中途卡死（卡死会让 Mod 失效直到重装）。更稳妥的免调试器方案是只构建、然后把归
档直接写进 `xs` 分区：

```sh
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
python3 -m esptool --chip esp32s3 --before default-reset --after hard-reset \
  write-flash 0xDF0000 "$MODDABLE/build/bin/esp32/debug/mod/mod.xsa"
```

`0xDF0000` 是分区补丁中 `xs` 分区的偏移；esptool 自带写入校验，复位后 host 直接
挂载新归档。随后从 watcher 电脑验收 Mod：

```sh
curl "http://$STACKCHAN_HOST/health"
curl "http://$STACKCHAN_HOST/api/status"
```

### 4. 配置并启动 watcher

```sh
cp "$MATCHDAY_DIR/config/kalshi_watchlist.example.json" \
  "$MATCHDAY_DIR/config/kalshi_watchlist.json"
```

编辑复制出的文件，确认：

- `stackchan_host` 与 `$STACKCHAN_HOST` 或设备局域网 IP 一致。
- `stackchan_transport` 是 `http`；手机设置中继不支持串口模式。
- `setup_server.enabled` 是 `true`。
- 端口 `8788` 未被占用。默认绑定 `127.0.0.1`，因此可选的本机管理页不会暴露到
  局域网。

检查 JSON 后，让 watcher 持续运行：

```sh
python3 -m json.tool "$MATCHDAY_DIR/config/kalshi_watchlist.json"
python3 "$MATCHDAY_DIR/tools/stackchan_kalshi_watch.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --watch
```

示例中的 `KXEXAMPLE-...` 是有意保留的占位 ticker。在手机选择真实比赛或手动
换成开放 ticker 之前，watcher 可能报告它们 missing。`--dry-run` 只会阻止写入
设备，仍会访问公开 API，不能当作离线安装验收。

### 5. 可选：启用局域网语音

在 macOS 的第二个终端里重新导出仓库路径，再以前台方式启动自带服务，便于直接
看到错误：

```sh
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
python3 "$MATCHDAY_DIR/tools/stackchan_tts_server.py" --host 0.0.0.0 --port 8787
```

保持这个终端运行。在另一个终端中先验收服务，再让设备连接 watcher 电脑的局域网
地址，而不是 `127.0.0.1`：

```sh
curl "http://127.0.0.1:8787/health"
export STACKCHAN_HOST=stackchan.local
export WATCHER_HOST=192.168.1.20
curl --request POST --data-binary "tts host $WATCHER_HOST:8787" \
  "http://$STACKCHAN_HOST/api/command"
curl --request POST --data-binary "say 比赛日准备好了" \
  "http://$STACKCHAN_HOST/api/command"
```

请在电脑防火墙中允许 TCP `8787` 入站。用 `say -v '?'` 查看 macOS 已安装的
声音；可通过 `STACKCHAN_TTS_ZH_VOICE`、`STACKCHAN_TTS_EN_VOICE` 和
`STACKCHAN_TTS_RATE` 覆盖默认值。TTS 不可达时，Mod 会自动回退到短提示音。

## 如何使用

1. **先启动 watcher。** 保持 watcher 电脑唤醒，并让 `--watch` 进程持续运行；
   手机、电脑和 Stack-chan 必须在同一个可信局域网。
2. **摸头唤出设置码。** 双击 Stack-chan 头顶的三段触摸条，屏幕会显示设置二维码；
   显示时轻点一次可关闭，90 秒后也会自动关闭。在固定的 host firmware 上，短按
   Power 键同样可以开关二维码。
3. **扫码选择比赛。** 用手机打开二维码，选择中文或 English、要看的比赛、支持
   球队（也可中立）、可选的赛前持仓（也可“没买”）以及播报语气，再点“开始
   看球”。有持仓时，Match Setup 会启用该队对应的 Kalshi 市场；选择“没买”时
   默认启用比赛双方列表中的第一个市场。持仓仍只是手动偏好，系统不会读取账户。
4. **等待确认。** 页面会先显示“已提交，等待 watcher”。watcher 会校验 ESPN 与
   Kalshi 的双方匹配，原子更新本地配置、热重载并确认设备；无需重启 watcher 或
   Stack-chan。
5. **开始共看。** 比赛期间，Stack-chan 会更新旗帜和概率，并用屏幕、表情、灯光、
   头部、提示音与可选语音响应比分和文字直播事件。
6. **没有球赛也能看盘。** 在同一页面粘贴 Kalshi event URL 或 ticker；该事件中
   成交最活跃的最多四个市场会显示在底部 ticker，比赛专属的旗帜概率条和 ESPN
   播报会暂时关闭。
7. **开会一键静音（老板键）。** 长按头顶触摸条约一秒即可切换静音：语音、音效、
   庆祝动作和警报闪灯全部停止，概率条、气泡和 ticker 继续无声更新，屏幕角落
   会常驻 `静音` / `MUTE` 标签直到恢复。也可用 `/api/command` 发送
   `mute on 60`（或控制面板的“mute 60m”按钮）定时静音一场会议，到点自动恢复
   并气泡提示；不限时静音重启后依然保留。

<table>
  <tr>
    <td align="center" width="38%">
      <img src="docs/images/photos/scan-setup-qr.jpg" alt="手机相机正在扫描 Stack-chan 屏幕上的设置二维码" width="220"><br>
      <sub>用手机扫描设备二维码，打开局域网设置页（图中为示例地址）。</sub>
    </td>
    <td align="center" width="62%">
      <img src="docs/images/photos/head-touch-mute.jpg" alt="手指长按 Stack-chan 头顶触摸条，设备显示静音和已静音提示" width="380"><br>
      <sub>长按头顶触摸条进入静音；语音、动作和警报灯停止，视觉信息继续更新。</sub>
    </td>
  </tr>
</table>

watcher 还可以每天主动提醒你扫码选比赛。相关配置为
`setup_server.daily_prompt_hour`（`-1` 关闭）、`prompt_minutes_before`、
`quiet_hours` 和 `lookahead_days`。

## 配置与语言

设置页会立即切换全部界面文案，并把语言持久化到设备。应用一场比赛后，watcher
产生的语音和气泡也会切换。顶层 `language` 可以是 `zh` 或 `en`。

面向用户的配置文本既可写成旧式字符串，也可写成中英对象：

```json
{
  "language": "en",
  "mac_voice": {"zh": "Tingting", "en": "Samantha"},
  "espn": {
    "commentary_style": "balanced",
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

watcher 会先查全局 `config/espn_player_catalog.json`。目录以稳定的 ESPN athlete
ID（例如 `espn:362150`）为主键，保存正式中英文名、经人工核实的 casual 昵称、
球星标记和可选进球口号。`balanced`、`professional` 与所有气泡始终使用正式名；
只有 casual 语音可以使用目录中的亲切昵称。旧配置里的 `player_names` 和
`star_chants` 继续有效，并优先于全局目录；自定义进球信号语音也仍支持上述本地化
格式。

无法匹配目录的球员会回退到 ESPN 原名，不音译、不猜昵称。ESPN roster 首次出现
或发生变化后，watcher 会在日志输出目录命中数、球星数和原名回退名单，便于赛前
补齐覆盖，而不会阻断播报。

### 三档播报语气

`espn.commentary_style` 是全局持久偏好，只接受 `casual`、`balanced` 或
`professional`。旧配置缺少该字段时默认使用 `balanced`。

| 配置值 | 语音 | 设备气泡 |
| --- | --- | --- |
| `casual` | 朋友陪看的口语表达，但完整保留核心事实 | 精炼事件摘要 |
| `balanced` | 清楚、自然，兼容此前的播报方式 | 精炼事件摘要 |
| `professional` | 先播核心事实，再用足球术语补充可靠解析出的 ESPN 细节 | 仍是精炼摘要，详细信息交给语音 |

三档都必须保留比赛时间、事件类型、球队、必要球员、事件结果和当前比分；点球、
红黄牌和换人也会保留必要参与者。“疑似进球”“等待文字直播确认”等不确定性在
任何语气下都不会被省略。专业档只会补充 ESPN 明确提供且 watcher 能可靠解析的
助攻或传中、射门方式和身体部位、场上或球门位置、门将扑救、定位球位置、换人或
伤情原因；不会直接朗读英文原文，也不会猜测缺失细节。

三档气泡原则上都保持“时间 + 球员/球队 + 事件 + 比分”，只允许标点和少量措辞
差异。

支持队决定日常陪看视角；普通射门、扑救、角球等攻防事件不会牵强讨论仓位。进球、
点球、红牌和赛果等重大 ESPN 事件才会追加持仓利好/利空；支持与持仓同向时合并成
一句自然反应，方向冲突时分别说明“心里支持谁”和“仓位受益或承压”。只有与所选
持仓队对应且由 Match Setup 标记为 `tracks_position` 的 Kalshi 市场，盘口变化和
疑似进球提醒才会使用持仓措辞；疑似事件仍保留“如果属实”和等待确认。

语气也覆盖比赛阶段和赛果、Kalshi 盘口突变与疑似进球提醒，并且只改变文案：TTS
音色和语速、音效、庆祝动作、表情、灯效、优先级与提醒开关均保持不变。比赛中可从
任一设置页即时切换；新生成的提醒会立即使用新语气，但不会重播 ESPN 旧事件，也
不会重置盘口基线、告警队列或轮询状态。

串口模式只适合直接命令/控制。如果使用它，需要安装 `pyserial` 并配置
`stackchan_serial_port`；手机设置、设备状态检测和 options/pending/ack 中继都
依赖 HTTP。

## 排障

- **设置页没有比赛：** 确认 watcher 正以 setup enabled 状态运行，并能访问两个
  公开 API。只有同时满足以下条件的比赛才会列出：ESPN 状态为 `pre`/`in`，并且
  配置的 `kalshi_series_ticker` 中存在能按双方队名匹配的开放 Kalshi 事件。
- **页面一直停在“等待 watcher”：** watcher 已停止、正在用串口模式、无法绑定
  本机 `8788`，或无法访问设备 TCP `80`。请先看 watcher 终端日志。
- **二维码打开了错误地址：** 重新生成 `mod/assets/setup/setup-qr.png` 并重装
  Mod。它是静态图片；仅修改屏幕上的 URL 不会改写二维码模块。
- **中文显示成方框：** 应用可选字体补丁，准备 CJK TTF，重新构建并烧录 host，
  然后重装 Mod。
- **没有语音：** 先看屏幕角落有没有 `静音` / `MUTE` 标签，或运行 `mute status`
  ——长按头顶触摸条会切换老板键。再检查 TTS `/health`、电脑防火墙，以及通过
  `/api/command` 返回的 `tts status`。视觉效果和提示音回退不依赖 TTS。
- **`npm run mod` 卡在 “Installing mod...”：** xsbug 协议安装中途卡死；被中断的
  安装会让 Mod 失效直到重装。退出 xsbug，改用安装章节里的“构建 +
  `esptool write-flash 0xDF0000`”方案——无需调试器且自带写入校验。
- **接着 xsbug 时设备冻结并掉网：** xsbug 在异常断点处会暂停整个运行时，WiFi、
  触摸和定时器全部停摆。日常运行请断开 `serial2xsbug`/xsbug；要无冻结地抓日志，
  用 `$MODDABLE/tools/xsbug-log` 无头模式。
- **`stackchan.local` 无法解析：** 改用设备局域网 IP 并重新生成二维码；最好在
  DHCP 中为它保留地址。
- **市场显示 missing：** 示例值只是占位符。请从设置页选择真实比赛，或用
  `python3 "$MATCHDAY_DIR/tools/stackchan_kalshi_watch.py" discover --query 关键词`
  返回的开放 ticker 替换。

## 设备 API

`GET /api/help` 会列出 `POST /api/command` 接受的纯文本命令：

```text
pkbar es 62 AA151B be 38 EF3340
balloon temp 8000 西班牙进球了！
voice favorite-goal 7号球员进球啦
celebrate goal 170 21 27
celebrate say 170 21 27 进球了！
celebrate result win 170 21 27 比赛结束
setup show http://stackchan.local/setup
say 你好
mute on 60 · mute off · mute status
face happy · look 8 -2 · idle look on · light flash 0 85 164
```

`GET /api/status` 会返回 Mod 版本、概率条、TTS、电源、网络和设置触发计数器。
`POST /api/control` 接受 JSON action。watcher 使用的设置接口为
`/api/match-setup`、`/options`、`/apply`、`/ack`、`/pending` 和
`/language`。

播报语气使用独立转发链路。设备接收 `POST /api/match-setup/style`，请求体为
`{"commentary_style":"casual|balanced|professional"}`，并通过现有 pending/ack
流程转发；watcher 本地管理服务的对应接口为 `POST /api/setup/style`，相同请求体
即可更新语气，`GET /api/setup/status` 会返回当前生效值。

## 开发

在本仓库运行完整测试：

```sh
cd "$MATCHDAY_DIR"
python3 -m unittest discover -s tools -p 'test_*.py'
node tools/test_stackchan_mod_web_behavior.mjs
```

在上游 `firmware/` 目录构建但不安装 Mod archive：

```sh
cd "$STACKCHAN_DIR/firmware"
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
```

可通过同一套 parser 回放法国—摩洛哥的 ESPN 历史。默认只预览命令；选择实际执行
前，请先停止持续运行的 watcher：

```sh
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json"
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --language en
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --execute
```

## 安全

设备 HTTP API 按设计不设认证并开放 CORS。只应在可信局域网使用，不要转发 TCP
`80`、`8787` 或 `8788`。设备没有 Wi-Fi 凭据时才会出现后备 AP
（`StackChan-Matchday` / `stackchan`）。启动 watcher 前，请通过 BLE 使用官方
[Stack-chan Web Console](https://stack-chan.github.io/web/) 配置 Wi-Fi。

## 致谢与许可证

- Shinya Ishikawa 的 [Stack-chan](https://github.com/stack-chan/stack-chan) —
  Apache-2.0。
- 旗帜 PNG 来源于 [flag-icons](https://github.com/lipis/flag-icons) — MIT；详见
  `mod/LICENSE-flag-icons.txt`。
- 本仓库 — [MIT](LICENSE)。
