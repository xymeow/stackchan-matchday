# Development

[English](development.md) | [简体中文](development.zh-CN.md)

## Repository map

- `mod/` — device mod, split into small JavaScript modules plus flag and QR
  assets.
- `host/` — required CoreS3 partition patch, optional CJK-font patch, and font
  preparation helper. These are build/resource changes; upstream runtime JS/C
  source remains unchanged.
- `tools/` — watcher, venue adapters (`stackchan_venues.py`: Kalshi +
  Polymarket normalized quotes and aggregation), local setup service, macOS
  TTS server, replay tool, serial helper, asset generator, and tests. Default
  HTTP needs only Python's standard library; serial transport additionally
  needs `pyserial`.
- `config/` — example watcher configuration, flag-pack definition, global
  ESPN player catalog, and the cross-venue pairing registry
  (`pairing_registry.json`, agent-proposed and human-confirmed).
- `docs/` — versioned user, API, development, product, and release guides.
- `agent-skills/` — repo-local agent skills; `market-pairing` fetches
  Kalshi/Polymarket/ESPN candidates and proposes pairing-registry entries.

## Test suites

Run all local tests from this repository:

```sh
cd "$MATCHDAY_DIR"
python3 -m unittest discover -s tools -p 'test_*.py'
node tools/test_stackchan_mod_web_behavior.mjs
```

The Python suite covers watcher behavior, Match Setup, commentary contracts,
and device-facing integration helpers. The Node suite exercises the mod web
surface without installing it on hardware.

Spoiler-protection changes should prove that Kalshi-derived alerts are
suppressed while market state and passive displays keep updating, confirmed
ESPN alerts remain eligible, queued market alerts are purged on hot enable,
and a later disable does not replay accumulated movement. Cover watcher-local
and device pending/ack paths, including the boolean `false` case.

## Build without installing

Build a mod archive from the upstream Stack-chan `firmware/` directory:

```sh
cd "$STACKCHAN_DIR/firmware"
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
```

CoreS3 requires `-f rgb565be`. The resulting debug archive is normally at
`$MODDABLE/build/bin/esp32/debug/mod/mod.xsa`. See
[Getting started](getting-started.md) before writing an archive to hardware;
its `0xDF0000` offset assumes this repository's host partition patch.

## Replay a recorded match

The replay tool runs the France–Morocco ESPN history through the same alert
parser. Preview is the default. Stop the continuous watcher before explicitly
executing commands on a connected device.

```sh
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json"
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --language en
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --execute
```

Use preview to inspect wording and event ordering without sending device
commands. `--execute` is intentionally opt-in because it produces real screen,
speech, light, and motion activity.

## Debugging on hardware

`npm run mod` uses the xsbug protocol. A stalled install can invalidate the mod
until it is reinstalled. More importantly, xsbug exception breakpoints pause
the entire device runtime, including Wi-Fi, touch, and timers; do not leave an
interactive xsbug session attached during unattended match watching.

Use `$MODDABLE/tools/xsbug-log` when you need logs without breakpoint freezes.
For a deterministic reinstall, follow the debugger-free build and esptool path
in [Getting started](getting-started.md). Field symptoms and short recovery
recipes are indexed in the
[Debugging and recovery Wiki page](https://github.com/xymeow/stackchan-matchday/wiki/Debugging-and-recovery).

## Documentation synchronization checklist

When behavior changes, update the applicable items together:

1. Example configuration in `config/kalshi_watchlist.example.json`.
2. The corresponding English and Chinese installation, configuration, API, or
   development guides.
3. User entry points in `README.md` and `README.zh-CN.md`, keeping only concise
   summaries and links there.
4. Release notes under `docs/releases/`.
5. `docs/commentary-styles-prd.md` when product rules change.
6. Environment-specific troubleshooting in the
   [Troubleshooting Wiki page](https://github.com/xymeow/stackchan-matchday/wiki/Troubleshooting)
   when useful, linked from the README documentation index.

English and Chinese headings, commands, and facts should remain aligned. The
Chinese text may use natural phrasing rather than translating sentence by
sentence.

## Security and local state

- Do not commit `config/kalshi_watchlist.json`, API keys, account information,
  device Wi-Fi credentials, or private TTS configuration.
- The Kalshi REST MVP does not require an API key. If future functionality
  requires authentication, read it from environment variables or a local file,
  never firmware.
- Keep device endpoints on a trusted LAN; tests must not expose TCP `80`,
  `8787`, or `8788` to the internet.
- Serial transport additionally requires `pyserial`. Do not mistake a runtime
  paused by a debugger for a network failure.
- Before testing real hardware, verify that no other watcher or replay process
  is sending commands at the same time.
