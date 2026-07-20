# Getting started

[English](getting-started.md) | [简体中文](getting-started.zh-CN.md)

This guide covers a first Stack-chan Matchday installation: preparing the
tested upstream Stack-chan checkout, installing the one-time host changes,
building the Matchday mod, starting the watcher, and optionally enabling LAN
speech.

Already running Matchday? Read the version-specific
[1.7.0](releases/1.7.0.md), [1.6.0](releases/1.6.0.md),
[1.5.0](releases/1.5.0.md), and
[1.4.0](releases/1.4.0.md) notes before rebuilding. The 1.7.0 multi-venue
aggregation is a watcher-only update. Spoiler protection on the
device phone page requires both the 1.6.0 watcher and mod, but not a host
reflash. The 1.5.0 support/position wording and global player catalog remain
watcher-only updates.

## Requirements

- A CoreS3-based Stack-chan with 16 MB flash and a USB data cable.
- Git, Python 3.10+, Node.js 20+ (Node.js 22 is the tested upstream version),
  npm, and `xz` on the build computer.
- Moddable SDK and ESP-IDF. The upstream `xs-dev` setup below installs and
  checks them.
- A phone and watcher computer on the same trusted LAN as Stack-chan.
- `qrencode` only when generating the device-specific setup QR.
- macOS only for the included `say`-based TTS server. Other systems can run
  without speech or provide a compatible `/say` WAV service.

Commands below use macOS/Linux shell syntax. Set two absolute paths once and
reuse them throughout installation:

```sh
mkdir -p "$HOME/src"
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
export STACKCHAN_DIR="$HOME/src/stack-chan"
```

## 1. Clone Matchday

```sh
git clone https://github.com/xymeow/stackchan-matchday.git "$MATCHDAY_DIR"
```

If it is already cloned, preserve your local configuration, update the code,
and read the [release notes](releases/) before deciding which components need
to be reinstalled.

## 2. Prepare and flash the host once

Every new CoreS3 host checkout needs the Matchday partition patch. Chinese
labels and balloons additionally need the optional CJK-font patch and prepared
font resource; an English-only installation may skip those two pieces.

Follow [Host preparation](../host/README.md) for the tested upstream commit,
dependency checks, partition and optional font patches, build, and flash
commands. That guide is the canonical source for host changes and explains why
the upstream runtime JS/C source remains unmodified.

The Matchday mod cannot be installed until the host contains the `xs`
partition. Host work is normally one-time; watcher-only updates do not require
reflashing it. If `npm run doctor` reports a missing platform prerequisite,
consult the upstream
[getting-started guide](https://github.com/stack-chan/stack-chan/blob/dev/v1.0/firmware/docs/getting-started.md)
and build only after `esp32` is listed as a supported target.

## 3. Generate the setup QR

The QR is a static image compiled into the mod, not generated at runtime. Give
Stack-chan a stable DHCP reservation, IP address, or resolvable mDNS name, then
generate the asset before installing the mod. Keep both PNG edges at or below
168 px so the title and URL still fit.

```sh
export STACKCHAN_HOST=stackchan.local
qrencode -s 4 -m 1 -o "$MATCHDAY_DIR/mod/assets/setup/setup-qr.png" \
  "http://$STACKCHAN_HOST/setup"
file "$MATCHDAY_DIR/mod/assets/setup/setup-qr.png"
```

If `file` reports an edge larger than 168 px, regenerate with `-s 3`.

Changing `stackchan_host` or the URL shown elsewhere does not rewrite this
compiled PNG. If the device address changes, regenerate the QR and reinstall
the mod.

## 4. Build and install the mod

### Debug-protocol installation

From the upstream `firmware/` directory:

```sh
cd "$STACKCHAN_DIR/firmware"
npm run mod --target=esp32:./platforms/m5stackchan_cores3 -- -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
```

`-f rgb565be` is required on CoreS3. Without it, flag colors are byte-swapped.

`npm run mod` installs over the xsbug debug protocol. It needs an xsbug
listener and may stall mid-write if the device is busy; a killed or stalled
write leaves the mod unavailable until it is reinstalled.

### Preferred without a debugger: write directly to `xs`

When no debugger is needed, prefer building the archive and writing it
directly to `xs` for a deterministic installation path:

```sh
cd "$STACKCHAN_DIR/firmware"
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
python3 -m esptool --chip esp32s3 --before default-reset --after hard-reset \
  write-flash 0xDF0000 "$MODDABLE/build/bin/esp32/debug/mod/mod.xsa"
```

`0xDF0000` is the `xs` partition offset created by the host patch. esptool
verifies the write; the host mounts the archive after the following reset. Do
not use this offset with other hardware or host builds unless their partition
table is confirmed to match.

Verify the device from the watcher computer:

```sh
curl "http://$STACKCHAN_HOST/health"
curl "http://$STACKCHAN_HOST/api/status"
```

## 5. Configure the watcher

Create a local configuration from the tracked example:

```sh
cp "$MATCHDAY_DIR/config/kalshi_watchlist.example.json" \
  "$MATCHDAY_DIR/config/kalshi_watchlist.json"
```

Check these values in the copy:

- `stackchan_host` matches `$STACKCHAN_HOST` or the device LAN IP.
- `stackchan_transport` is `http`; phone setup does not work over serial.
- `setup_server.enabled` is `true`.
- Port `8788` is free. The default `127.0.0.1` binding keeps the optional
  watcher admin page local to that computer.

Validate the JSON and start the continuous watcher:

```sh
python3 -m json.tool "$MATCHDAY_DIR/config/kalshi_watchlist.json"
python3 "$MATCHDAY_DIR/tools/stackchan_kalshi_watch.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --watch
```

The example `KXEXAMPLE-...` tickers are deliberate placeholders. Until you
select a live match from the phone or enter real open tickers, the watcher may
report them missing. `--dry-run` suppresses device writes but still calls the
public APIs; it is not an offline installation test.

See [Configuration and operation](configuration.md) for language, commentary,
support and position behavior, player names, mute, and standalone mode.

## 6. Optional: enable LAN speech

On macOS, start the included TTS server in a second terminal and leave it in
the foreground so errors remain visible:

```sh
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
python3 "$MATCHDAY_DIR/tools/stackchan_tts_server.py" --host 0.0.0.0 --port 8787
```

Verify it, then point the device at the watcher's LAN address—not
`127.0.0.1`:

```sh
curl "http://127.0.0.1:8787/health"
export STACKCHAN_HOST=stackchan.local
export WATCHER_HOST=192.168.1.20
curl --request POST --data-binary "tts host $WATCHER_HOST:8787" \
  "http://$STACKCHAN_HOST/api/command"
curl --request POST --data-binary "say Matchday ready" \
  "http://$STACKCHAN_HOST/api/command"
```

Allow inbound TCP `8787` through the computer firewall. `say -v '?'` lists
installed macOS voices. Override the defaults with
`STACKCHAN_TTS_ZH_VOICE`, `STACKCHAN_TTS_EN_VOICE`, and
`STACKCHAN_TTS_RATE`. If TTS is unreachable, the mod falls back to short tone
patterns.

## Verification and field troubleshooting

Verify the mod with `/health` and `/api/status`, then confirm that a phone
selection moves from pending to acknowledged while the watcher is running.
Preserve the watcher output and status response before restarting or
reflashing anything.

Use [Troubleshooting](https://github.com/xymeow/stackchan-matchday/wiki/Troubleshooting)
for symptom-first checks covering networking, setup, QR, TTS, CJK text, and
markets. For xsbug freezes, interrupted installs, and device recovery, use
[Debugging and recovery](https://github.com/xymeow/stackchan-matchday/wiki/Debugging-and-recovery).
This versioned guide remains the canonical reference for commands and
partition-dependent values.
