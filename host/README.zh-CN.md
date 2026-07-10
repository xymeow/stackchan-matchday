# Host firmware 构建改动

[English](README.md) | [简体中文](README.zh-CN.md)

Stack-chan Matchday 使用官方
[`stack-chan/stack-chan`](https://github.com/stack-chan/stack-chan) host
runtime，不修改 runtime JS/C 源码。不过 CoreS3 构建仍需一个分区 manifest
补丁，才能为 Mod 提供安装位置；另有一个用于中文显示的可选资源补丁。

| 补丁 | 是否必需 | 用途 |
| --- | --- | --- |
| `patches/0001-Add-xs-mod-partition-for-M5StackChan-CoreS3.patch` | 必需 | 在 16 MB CoreS3 Flash 的 `0xDF0000` 添加 2 MB `xs` 分区。没有 type `0x40` / subtype `1` 分区，`mcrun` 无处安装 Mod。 |
| `patches/0002-Add-optional-StackChanCN-24-GB2312-font-resource.patch` | 仅中文界面需要 | 构建 24 px 单色 `StackChanCN-24` 资源。没有它时，Mod 会回退到无法显示 CJK 的 `OpenSans-Regular-24`。 |

这些补丁以固定上游 commit
`ded5ca94ef50411aec213b85a23d1afe72d4c29e` 为测试基线。请 pin 这个 commit，
避免 `dev/v1.0` 移动后影响复现。

## 1. 准备上游 checkout

```sh
mkdir -p "$HOME/src"
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
export STACKCHAN_DIR="$HOME/src/stack-chan"

git clone https://github.com/stack-chan/stack-chan.git "$STACKCHAN_DIR"
cd "$STACKCHAN_DIR"
git switch --detach ded5ca94ef50411aec213b85a23d1afe72d4c29e

cd "$STACKCHAN_DIR/firmware"
npm ci
npm run setup -- --device=esp32
npm run doctor
```

只有当 `npm run doctor` 把 `esp32` 列为 supported target 后，才继续。

## 2. 应用必需的分区补丁

请使用干净 checkout，并先只应用补丁 1：

```sh
cd "$STACKCHAN_DIR"
git am "$MATCHDAY_DIR/host/patches/0001-Add-xs-mod-partition-for-M5StackChan-CoreS3.patch"
```

## 3. 可选：加入中文字体

示例配置默认使用中文，因此中文用户应应用补丁 2。纯英文安装可以跳过本节，并将
watcher 的语言设为 `en`。

```sh
cd "$STACKCHAN_DIR"
git am "$MATCHDAY_DIR/host/patches/0002-Add-optional-StackChanCN-24-GB2312-font-resource.patch"
python3 "$MATCHDAY_DIR/host/prepare_cjk_font.py" "$STACKCHAN_DIR"
```

macOS 上，脚本默认使用 Arial Unicode。其他系统或需要更换字体时，可显式传入任意
支持 CJK 的 TTF：

```sh
python3 "$MATCHDAY_DIR/host/prepare_cjk_font.py" "$STACKCHAN_DIR" \
  --ttf /absolute/path/to/NotoSansSC-Regular.ttf
```

系统字体通常不允许重新分发，因此 TTF 不会提交进仓库。如果已应用补丁 2 却没有
准备 `StackChanCN.ttf`，host 构建会直接失败，而不是悄悄省略中文字形。

## 4. 构建并烧录 host

```sh
cd "$STACKCHAN_DIR/firmware"
export PATH="$PWD/node_modules/.bin:$PATH"
mcconfig -d -m -p esp32:./platforms/m5stackchan_cores3 -t deploy \
  "$PWD/stackchan/manifest_m5stackchan_cores3.json"
```

完成这次 host 烧录后，回到根 README 生成设备专属二维码并安装 Matchday Mod。
以后仅重新构建 Mod 时，不需要再次烧录 host。
