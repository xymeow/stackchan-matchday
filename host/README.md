# Host firmware build changes

[English](README.md) | [简体中文](README.zh-CN.md)

Stack-chan Matchday uses the official
[`stack-chan/stack-chan`](https://github.com/stack-chan/stack-chan) host runtime.
It does not change the runtime JS/C source, but a CoreS3 build still needs one
partition-manifest patch so a mod can be installed. A second resource patch is
available for Chinese text.

| Patch | Required? | Purpose |
| --- | --- | --- |
| `patches/0001-Add-xs-mod-partition-for-M5StackChan-CoreS3.patch` | Yes | Adds a 2 MB `xs` partition at `0xDF0000` on 16 MB CoreS3 flash. Without a type `0x40` / subtype `1` partition, `mcrun` has nowhere to install a mod. |
| `patches/0002-Add-optional-StackChanCN-24-GB2312-font-resource.patch` | Chinese UI only | Builds a 24 px monochrome `StackChanCN-24` resource. Without it, the mod falls back to `OpenSans-Regular-24`, which does not render CJK text. |

The patches are tested against upstream commit
`ded5ca94ef50411aec213b85a23d1afe72d4c29e`. Pin that commit so the installation
remains reproducible even if `dev/v1.0` moves.

## 1. Prepare the upstream checkout

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

Continue only after `npm run doctor` lists `esp32` as a supported target.

## 2. Apply the required partition patch

Use a clean checkout and apply only patch 1 first:

```sh
cd "$STACKCHAN_DIR"
git am "$MATCHDAY_DIR/host/patches/0001-Add-xs-mod-partition-for-M5StackChan-CoreS3.patch"
```

## 3. Optional: add the Chinese font

Chinese is the example configuration's default language, so Chinese users
should apply patch 2. Pure-English installations can skip this section and set
the watcher language to `en`.

```sh
cd "$STACKCHAN_DIR"
git am "$MATCHDAY_DIR/host/patches/0002-Add-optional-StackChanCN-24-GB2312-font-resource.patch"
python3 "$MATCHDAY_DIR/host/prepare_cjk_font.py" "$STACKCHAN_DIR"
```

On macOS the helper uses Arial Unicode by default. On other systems, or to use
a different typeface, pass any CJK-capable TTF explicitly:

```sh
python3 "$MATCHDAY_DIR/host/prepare_cjk_font.py" "$STACKCHAN_DIR" \
  --ttf /absolute/path/to/NotoSansSC-Regular.ttf
```

The TTF stays uncommitted because system-font licenses usually do not permit
redistribution. If patch 2 is present but `StackChanCN.ttf` has not been
prepared, the host build will fail rather than silently omit Chinese glyphs.

## 4. Build and deploy the host

```sh
cd "$STACKCHAN_DIR/firmware"
export PATH="$PWD/node_modules/.bin:$PATH"
mcconfig -d -m -p esp32:./platforms/m5stackchan_cores3 -t deploy \
  "$PWD/stackchan/manifest_m5stackchan_cores3.json"
```

After this one-time host flash, return to the root README to generate the
device-specific QR and install the Matchday mod. Rebuilding only the mod does
not require reflashing the host.
