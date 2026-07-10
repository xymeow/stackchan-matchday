# Host firmware changes

The matchday mod runs on an unmodified [stack-chan](https://github.com/stack-chan/stack-chan)
host **codebase** — no source patches. Two build-level additions are still
needed, shipped here as reviewable git patches instead of edit scripts:

| Patch | Why |
| --- | --- |
| `patches/0001-Add-xs-mod-partition-for-M5StackChan-CoreS3.patch` | The stock CoreS3 partition table has no `xs` (type 0x40) partition, so **no mod can install at all**. This reserves 2MB at `0xDF0000` on the 16MB flash. Required. |
| `patches/0002-Add-optional-StackChanCN-24-GB2312-font-resource.patch` | Compiles a 24px monochrome GB2312 font (`StackChanCN-24`) into the host so balloons and labels render Chinese. Optional — without it the mod falls back to `OpenSans-Regular-24` and CJK text will not render. |

Apply to a checkout of `stack-chan` (branch `dev/v1.0`):

```sh
cd /path/to/stack-chan
git am /path/to/stackchan-matchday/host/patches/*.patch
```

If you applied the font patch, prepare the (uncommitted) TTF before building:

```sh
python3 /path/to/stackchan-matchday/host/prepare_cjk_font.py /path/to/stack-chan
```

macOS Arial Unicode is the default source and reproduces the original look;
pass `--ttf /path/to/NotoSansSC-Regular.ttf` (or any CJK-capable TTF) on other
systems. The TTF is never committed because system fonts are not
redistributable.

Then build and deploy the host once, from the checkout's `firmware/` directory:

```sh
npm ci
export PATH="$PWD/node_modules/.bin:$PATH"
mcconfig -d -m -p esp32:./platforms/m5stackchan_cores3 -t deploy "$PWD/stackchan/manifest_m5stackchan_cores3.json"
```

Devices that were flashed with the earlier stackchan-kalshi tooling already
have this exact partition layout and font, so the mod installs on them
directly — no host reflash needed.
