# 开发指南

[English](development.md) | [简体中文](development.zh-CN.md)

本文面向修改 watcher、Matchday Mod、host 构建补丁或文档的维护者和 AI agent。

## 组件边界

| 目录 | 责任 | 不应承担的责任 |
| --- | --- | --- |
| `tools/` | 数据获取、解析、状态、文案、设置服务、TTS、回放和测试 | 设备 UI 与 host runtime |
| `mod/` | 手机设置中继、设备显示、语音、动作、灯效和资源 | 推断 ESPN 事实或生成专业文案 |
| `host/` | CoreS3 分区补丁、可选 CJK 字体补丁与准备脚本 | 修改上游 runtime JS/C 源码 |
| `config/` | 示例 watcher 配置、旗帜定义、全局球员目录 | 运行时秘密或真实账户信息 |
| `docs/` | 版本化安装、配置、接口、PRD 和升级说明 | 环境相关且易变的排障记录 |

构建参数、分区偏移、接口和配置行为必须保存在仓库文档并随代码评审。GitHub Wiki
适合 xsbug、mDNS、防火墙和串口抓日志等环境经验，但不应成为版本绑定事实的唯一来源。

## 本地测试

从 Matchday 仓库根目录运行完整测试：

```sh
cd "$MATCHDAY_DIR"
python3 -m unittest discover -s tools -p 'test_*.py'
node tools/test_stackchan_mod_web_behavior.mjs
```

Python 测试覆盖 watcher、Match Setup、结构化事实、文案和投递行为；Node 测试检查
设备 Web/API 行为。修改跨越 watcher 与 Mod 时，两组都要运行。

建议按改动范围补充针对性测试：

- 三档对同一事件必须包含相同的球队、球员、结果和比分。
- 疑似进球与等待确认在所有语气中保持不确定性。
- 专业细节只来自可可靠解析的 ESPN 字段；缺失值不能触发猜测。
- 只切换语气不能重复旧事件或重置盘口、告警与轮询状态。
- 防剧透开启后应抑制 Kalshi 派生提醒，但继续更新盘口状态与常驻显示；ESPN 已确认
  事件仍可投递。热开启需要清空待播盘口提醒，之后关闭不能补播累积变化。
- watcher 本机与设备 pending/ack 两条链路都要覆盖，尤其要测试布尔值 `false`。
- Match Setup 需要覆盖设备 pending/ack 与 watcher 原子更新两端。
- 投递失败测试应验证退避和有限重试，避免设备重启风暴。

## 构建 Mod 归档

在上游 Stack-chan `firmware/` 目录构建但不安装：

```sh
cd "$STACKCHAN_DIR/firmware"
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
```

CoreS3 的像素格式必须是 `rgb565be`。完整安装与分区写入流程见
[安装与升级](getting-started.zh-CN.md)；不要在开发文档复制另一份偏移说明。

## 回放 ESPN 历史比赛

`stackchan_match_replay.py` 使用和 watcher 相同的 parser 回放法国—摩洛哥 ESPN
历史。默认只预览命令；执行前先停止持续运行的 watcher，避免两个进程同时控制设备。

```sh
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json"
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --language en
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --execute
```

先用默认预览核对命令、语言、球员名、比分和不确定性，再使用 `--execute` 做设备端
验收。涉及 TTS 与动作串行时，还要观察命令到达顺序和设备状态轮询频率。

## 硬件调试

`npm run mod` 使用 xsbug 协议。安装中断可能使 Mod 暂时不可用，必须重新安装；
更重要的是，xsbug 的异常断点会暂停整个设备 runtime，包括 Wi-Fi、触摸和定时器。
无人值守看球时不要保持交互式 xsbug 会话连接。

需要不触发断点暂停的日志时，使用 `$MODDABLE/tools/xsbug-log`。需要确定性重装时，
按照[安装与升级](getting-started.zh-CN.md)中的无调试器构建与 esptool 流程操作。
现场症状与安全恢复顺序由
[调试与恢复 Wiki 页面](https://github.com/xymeow/stackchan-matchday/wiki/Debugging-and-recovery)
维护。

## 文档同步清单

行为改动完成时，根据影响面更新：

1. 示例配置 `config/kalshi_watchlist.example.json`。
2. 对应的中英文安装、配置、API 或开发文档。
3. 用户入口 `README.md` 与 `README.zh-CN.md`，但只保留摘要和链接。
4. 版本说明 `docs/releases/`。
5. 如果产品规则变化，更新 `docs/commentary-styles-prd.md`。
6. 环境型排障经验可补到
   [常见问题与排障 Wiki 页面](https://github.com/xymeow/stackchan-matchday/wiki/Troubleshooting)，
   并从 README 文档索引链接。

中英文文档的标题、命令和事实应保持一致；中文可以自然表达，不需要逐句直译。

## 安全与本地状态

- 不提交 `config/kalshi_watchlist.json`、API 密钥、账户信息、设备 Wi-Fi 凭据或私有
  TTS 配置。
- Kalshi REST MVP 不需要 API key；未来需要认证时，应从环境变量或本地文件读取，
  不要写入 firmware。
- 设备接口仅用于可信局域网，不要在测试中把 TCP `80`、`8787` 或 `8788` 暴露到
  公网。
- 串口模式需要额外的 `pyserial`；不要让调试器暂停运行时后误判成网络故障。
- 测试真实设备前，先确认没有另一个 watcher 或回放进程同时发送命令。
