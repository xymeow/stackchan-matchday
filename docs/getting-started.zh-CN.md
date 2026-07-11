# 安装与升级

[English](getting-started.md) | [简体中文](getting-started.zh-CN.md)

本文覆盖 Matchday Mod、设备二维码、Python watcher 和可选局域网 TTS。官方
Stack-chan host 的分区补丁、CJK 字体和首次烧录由
[Host firmware 构建改动](../host/README.zh-CN.md)维护，本文不重复那组命令。

## 安装结果

完成后应有四个彼此独立的部分：

1. 官方 Stack-chan host，加入 Matchday 所需的 `xs` 分区和可选 CJK 字体。
2. 安装在 `xs` 分区中的 Matchday Mod。
3. 在同一可信局域网持续运行的 Python watcher。
4. 可选的局域网 TTS 服务；它不可用时视觉反馈和短提示音仍然工作。

watcher 负责数据解析和文案，Mod 负责设备显示、语音、动作和手机设置中继。更新
watcher 文案通常不需要重新烧录 host。

## 环境要求

- 基于 CoreS3、配备 16 MB Flash 的 Stack-chan，以及一根可传数据的 USB 线。
- Git、Python 3.10+、Node.js 20+（上游已测试 Node.js 22）、npm 和 `xz`。
- Moddable SDK 与 ESP-IDF；上游 `xs-dev` 会安装并检查它们。
- 手机、watcher 电脑和 Stack-chan 位于同一个可信局域网。
- 生成设备专属二维码时需要 `qrencode`。
- 仓库自带的 `say` TTS 服务仅支持 macOS；其他系统可不启用语音，或提供兼容的
  `/say` WAV 服务。

下列命令适用于 macOS/Linux shell。先统一设置绝对路径：

```sh
mkdir -p "$HOME/src"
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
export STACKCHAN_DIR="$HOME/src/stack-chan"
```

每次打开新终端后，都需要重新导出这些变量。

## 1. 克隆 Matchday

```sh
git clone https://github.com/xymeow/stackchan-matchday.git "$MATCHDAY_DIR"
```

如果已经克隆，请保留本地配置，更新代码后先查看
[版本说明](releases/)再决定哪些组件需要重装。

## 2. 准备并烧录 host

严格按照 [host/README.zh-CN.md](../host/README.zh-CN.md) 操作。该文档维护以下
版本绑定事实：

- 经验证的上游 commit；
- CoreS3 `xs` 分区补丁和 `0xDF0000` 偏移；
- 可选 `StackChanCN-24` 字体资源；
- 上游依赖检查、host 构建与烧录命令。

每个新的上游 checkout 都要应用分区补丁。中文界面还要准备支持 CJK 的 TTF 并应用
字体补丁。成功烧录一次后，仅更新 Matchday Mod 或 watcher 不必重复烧录 host。
如果 `npm run doctor` 报告平台依赖缺失，请查阅上游
[环境配置说明](https://github.com/stack-chan/stack-chan/blob/dev/v1.0/firmware/docs/getting-started.md)，
并在 `esp32` 被列为 supported target 后再构建。

## 3. 生成设备二维码

二维码作为静态 PNG 编译进 Mod，运行时不会自动重画。先给设备设置稳定的 DHCP
保留地址、局域网 IP 或可解析的 mDNS 名称，然后在安装 Mod 前生成二维码：

```sh
export STACKCHAN_HOST=stackchan.local
qrencode -s 4 -m 1 -o "$MATCHDAY_DIR/mod/assets/setup/setup-qr.png" \
  "http://$STACKCHAN_HOST/setup"
file "$MATCHDAY_DIR/mod/assets/setup/setup-qr.png"
```

宽和高都不能超过 168 px，否则标题和 URL 可能无法完整显示。如果 `file` 显示任一
边超过 168 px，请改用 `-s 3` 重新生成。

修改 `stackchan_host` 或屏幕上的 URL 不会改写这个 PNG。设备地址发生变化后，需要
重新生成二维码并重装 Mod。

## 4. 构建并安装 Mod

### 调试协议安装

在上游 `firmware/` 目录运行：

```sh
cd "$STACKCHAN_DIR/firmware"
npm run mod --target=esp32:./platforms/m5stackchan_cores3 -- -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
```

CoreS3 必须使用 `-f rgb565be`，否则旗帜颜色会发生字节序错位。`npm run mod` 通过
xsbug 调试协议安装，需要 xsbug 正在监听；设备忙时可能在写入中途卡住。

### 推荐：直接写入 `xs` 分区

不需要调试器时，先构建归档，再由 esptool 直接写入分区：

```sh
cd "$STACKCHAN_DIR/firmware"
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
python3 -m esptool --chip esp32s3 --before default-reset --after hard-reset \
  write-flash 0xDF0000 "$MODDABLE/build/bin/esp32/debug/mod/mod.xsa"
```

`0xDF0000` 来自仓库的 host 分区补丁。esptool 会校验写入，复位后 host 直接挂载新
归档。不要在没有确认分区表一致时把该偏移用于其他硬件或 host 构建。

### 验收 Mod

从 watcher 电脑检查：

```sh
curl "http://$STACKCHAN_HOST/health"
curl "http://$STACKCHAN_HOST/api/status"
```

两者都应返回成功响应。若失败，请先确认设备已连入同一局域网，再查
[排障与 FAQ](https://github.com/xymeow/stackchan-matchday/wiki)。

## 5. 配置并启动 watcher

复制示例配置：

```sh
cp "$MATCHDAY_DIR/config/kalshi_watchlist.example.json" \
  "$MATCHDAY_DIR/config/kalshi_watchlist.json"
```

至少确认以下字段：

- `stackchan_host` 与 `$STACKCHAN_HOST` 或设备局域网 IP 一致。
- `stackchan_transport` 是 `http`；手机设置中继不支持串口模式。
- `setup_server.enabled` 是 `true`。
- 本机端口 `8788` 未被占用。默认绑定 `127.0.0.1`，不会暴露到局域网。

检查 JSON 并持续运行 watcher：

```sh
python3 -m json.tool "$MATCHDAY_DIR/config/kalshi_watchlist.json"
python3 "$MATCHDAY_DIR/tools/stackchan_kalshi_watch.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --watch
```

示例中的 `KXEXAMPLE-...` 是有意保留的占位 ticker。在手机选择真实比赛或手动换成
开放 ticker 之前，watcher 可能报告它们 missing。`--dry-run` 只阻止写入设备，
仍会访问公开 API，不能当作离线安装验收。

watcher 启动后，双击 Stack-chan 头顶触摸条或短按 Power 键，扫码选择比赛。页面
应先显示“已提交，等待 watcher”，随后变为已确认。完整行为见
[配置与播报](configuration.zh-CN.md)。

## 6. 可选：启用局域网 TTS

在 macOS 的第二个终端启动自带服务，前台运行便于看到错误：

```sh
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
python3 "$MATCHDAY_DIR/tools/stackchan_tts_server.py" --host 0.0.0.0 --port 8787
```

保持该终端运行。在另一个终端先验收服务，再让设备连接 watcher 电脑的局域网地址，
而不是 `127.0.0.1`：

```sh
curl "http://127.0.0.1:8787/health"
export STACKCHAN_HOST=stackchan.local
export WATCHER_HOST=192.168.1.20
curl --request POST --data-binary "tts host $WATCHER_HOST:8787" \
  "http://$STACKCHAN_HOST/api/command"
curl --request POST --data-binary "say 比赛日准备好了" \
  "http://$STACKCHAN_HOST/api/command"
```

请在电脑防火墙中允许 TCP `8787` 入站。用 `say -v '?'` 查看 macOS 已安装声音；
`STACKCHAN_TTS_ZH_VOICE`、`STACKCHAN_TTS_EN_VOICE` 和
`STACKCHAN_TTS_RATE` 可覆盖默认值。TTS 不可达时，Mod 会自动回退到短提示音。

## 升级边界

| 变更 | 通常需要更新 |
| --- | --- |
| watcher 文案、球员目录、解析和轮询 | watcher 代码与配置；重启 Python 进程 |
| Mod 页面、设备动作、命令或资源 | 重新构建并安装 Mod |
| `xs` 分区或 CJK host 字体 | 重新应用补丁并烧录 host |
| macOS TTS 服务实现 | 重启 TTS 服务；不需要刷设备 |

具体版本可能跨越多个边界。以对应的[版本说明](releases/)为准，不要仅根据版本号猜测
是否需要刷机。
