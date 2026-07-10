# Stack-chan Matchday

A lightweight [Stack-chan](https://github.com/stack-chan/stack-chan) mod plus a
LAN watcher that turn the robot into a World Cup co-watching companion: it
keeps both teams' Kalshi advance-market probabilities on screen, announces
goals and key ESPN live-commentary events with speech, balloons, lights, and a
safe little head dance, and lets you pick the next match from your phone.

> **Product boundary.** This is a read-only match companion. It does not
> trade, does not read any Kalshi account, and does not give betting advice.
> `position_team` is a manually entered preference used only to choose the
> post-match reaction — the system never sees a real position. ESPN and Kalshi
> endpoints used here are public but unofficial; they can lag behind the TV
> feed and may change without notice. Poll them politely.

```text
Kalshi public REST ------+
                         +--> Python watcher (Mac/LAN host) --HTTP--> Stack-chan mod
ESPN scoreboard/summary -+          |                                   |
                                    |                                   +--> probability bar + flags
                                    +--> fixture discovery              +--> balloons / tones / TTS
                                    +--> priority alert queue           +--> goal & result celebrations
                                                                        +--> /setup phone page
```

## Repository layout

- `mod/` — the device mod (seven JS modules + flag/QR assets). Builds to
  ~236KB, fits a standard 256KB `xs` partition; no host source patches
  required.
- `host/` — two reviewable git patches for the host build (mod partition for
  CoreS3 — required; optional `StackChanCN-24` CJK font) and the font prep
  script.
- `tools/` — the Kalshi/ESPN watcher, phone-setup service, LAN TTS server,
  match replay, shared i18n helpers, CLI control helper, flag-asset
  generator, and tests (stdlib only, no pip installs).
- `config/` — example watchlist and flag-pack definitions. Copy
  `kalshi_watchlist.example.json` to `kalshi_watchlist.json` (gitignored) and
  edit.

## Requirements

- M5Stack CoreS3-based Stack-chan (16MB flash)
- [Moddable SDK](https://github.com/Moddable-OpenSource/moddable) + ESP-IDF
  (easiest via [xs-dev](https://github.com/HipsterBrown/xs-dev)), Node.js
- A checkout of `stack-chan/stack-chan` (or a fork), branch `dev/v1.0`
- Python 3.10+ on the machine that runs the watcher (macOS `say` powers the
  default TTS server; any WAV-over-HTTP TTS works)

## Install

**1. Host firmware (once).** See [host/README.md](host/README.md): apply the
partition patch (and optionally the CJK font patch), then build and deploy the
stock host. Devices flashed with the earlier `stackchan-kalshi` tooling
already have this layout and font — skip straight to step 2.

**2. Mod.** From the stack-chan checkout's `firmware/` directory:

```sh
npm run mod --target=esp32:./platforms/m5stackchan_cores3 -- -f rgb565be \
  /path/to/stackchan-matchday/mod/manifest.json
```

`-f rgb565be` is required on CoreS3; without it the flag colors byte-swap.

**3. Point speech at your TTS server** (optional but recommended):

```sh
launchctl submit -l local.stackchan.tts -- /usr/bin/python3 "$PWD/tools/stackchan_tts_server.py" --host 0.0.0.0 --port 8787
curl -X POST --data-binary 'tts host <your-lan-ip>:8787' http://<stackchan-ip>/api/command
```

The mod streams speech through the host's stock `tts-remote` module and reads
the host's native `tts` preference domain, so changing the IP later is one
command — no rebuild, no reboot. Without a TTS server everything still works;
speech falls back to short tone patterns.

**4. Watcher.**

```sh
cp config/kalshi_watchlist.example.json config/kalshi_watchlist.json
python3 tools/stackchan_kalshi_watch.py --config config/kalshi_watchlist.json --once --dry-run
python3 tools/stackchan_kalshi_watch.py --config config/kalshi_watchlist.json --watch
```

With `setup_server.enabled: true`, open `http://<stackchan-ip>/setup` on a
phone in the same LAN to pick the next match, your team, and an optional
pregame position; the watcher validates and hot-reloads without restarting.

**Waking the setup QR on the device.** Double-tap the three-zone touch bar on
top of the head to show the setup QR; while it is visible, tap the top bar
once to hide it. Briefly pressing the physical Power button does the same on
hosts built from `dev/v1.0` commit `ded5ca9` (power-button support) or later.
The mod reads the Si12T top bar's raw 0–3 intensity samples itself with
hysteresis (press ≥ 2, release ≤ 1) instead of the host gesture recognizer,
so capacitive baseline drift on a warm device neither fires false taps nor
wedges the detector. `GET /api/status` → `setup.trigger.touch` exposes the
live intensity/position/tap counters for tuning the thresholds in
`mod/mod.js` without guesswork.

`mod/assets/setup/setup-qr.png` encodes the device setup URL and is
device-specific; regenerate it for your own address with e.g.
`qrencode -s 4 -m 1 -o mod/assets/setup/setup-qr.png "http://<stackchan-ip>/setup"`
and rebuild the mod.

## Language

Set the top-level `language` to `"zh"` or `"en"`. This selects one complete
output language for watcher-generated speech and balloons; Chinese remains the
default for existing configs.

The phone setup page has a `中文 / English` selector that switches the whole
page — section titles, match names, status lines — and the on-device QR
overlay **immediately** (`POST /api/match-setup/language`), and is persisted
across device reboots. A language picked on the page wins over whatever
language the watcher pushes with its fixture options; watcher speech and
balloons follow the selection once a match is applied with **Start watching**
(the watcher's poll endpoint `GET /api/match-setup/pending` also reports the
device language for future hot-switching).

You can also preview another language without editing the config file:

```sh
python3 tools/stackchan_kalshi_watch.py --config config/kalshi_watchlist.json \
  --language en --once --dry-run
python3 tools/stackchan_match_replay.py --config config/kalshi_watchlist.json \
  --language en
```

User-facing config values accept either a legacy string or a localized object:

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

The same leaf format works for `player_names`, `star_chants`, and custom goal
signal speeches. A legacy string is intentionally used verbatim in either
mode, so old configs never change meaning; use the object form when both
languages are wanted. Missing English names fall back to ESPN's source name,
and missing chants fall back to the built-in English goal sentence. The phone
setup service writes bilingual labels and goal-signal text when switching
matches.

The LAN TTS server automatically selects Tingting for Chinese text and
Samantha for English text (override them with `STACKCHAN_TTS_ZH_VOICE` and
`STACKCHAN_TTS_EN_VOICE`). The `mac_voice` field is also localizable for the
direct macOS `say` transport.

## What changed vs. the original stackchan_control mod

This is the slimmed-down successor of the mod in
[`stackchan-kalshi`](https://github.com/xymeow/stackchan-kalshi) (v0.14.0,
3178-line single file, 1.3MB, five host patch scripts):

- **No host source patches.** The marquee speech balloon is drawn by the mod
  itself; remote TTS uses the host's stock module and native preferences; the
  CJK font became one optional, reviewable host commit. The old
  `apply_official_*.py` scripts are all retired.
- **No embedded audio.** The 1.1MB MAUD voice pack is gone; every legacy clip
  id maps to a tone pattern, and player-specific lines stream from the LAN TTS
  server. (A "fat" variant with embedded crowd audio can come back later as an
  opt-in.)
- **Watch-focused.** MCP server, image-avatar packs, drawer buttons, and the
  decorative top-touch/IMU reactions were dropped; the upstream host's default
  mod and the official `image_avatar_lite` / `mcp` mods cover those better.
  The top touch bar is still used for one thing that matters here: waking the
  setup QR with a double tap.
- **Same wire protocol.** `pkbar`, `balloon temp`, `clip`, `voice`,
  `celebrate goal|result`, `light flash`, `setup show`, and the match-setup
  endpoints are unchanged, so existing watcher configs keep working; the
  watcher also still drives the legacy mod (>= 0.10.0).

## Command surface

`POST /api/command` with plain text (`GET /api/help` lists everything):

```text
pkbar es 62 AA151B be 38 EF3340      # persistent top probability bar
balloon temp 8000 西班牙进球了！        # marquee balloon, auto-hide
voice favorite-goal 7号球员进球啦      # remote TTS, tone fallback
celebrate goal 170 21 27             # dance + light + voice
celebrate say 170 21 27 姆巴佩进球啦   # speech + dance + light, synchronized
celebrate result win 170 21 27 比赛结束 # optional synchronized result speech
setup show http://<stackchan-ip>/setup
say 你好                              # balloon + TTS
face happy · look 8 -2 · idle look on · light flash 0 85 164
```

`GET /api/status` returns mod name/version, probability bar, TTS, power,
network state, and the setup-trigger touch counters. `POST /api/control`
accepts the JSON action form. The watcher-facing match-setup endpoints are
`/api/match-setup` (+ `/options`, `/apply`, `/ack`, `/pending`,
`/language`).

## Development

```sh
# All test suites (stdlib unittest)
for t in tools/test_*.py; do python3 "$t"; done
python3 -m tools.test_stackchan_tts_server

# Build the mod archive without installing (from the stack-chan checkout's firmware/)
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  /path/to/stackchan-matchday/mod/manifest.json
```

To replay the France–Morocco ESPN history (event `760510`) through the same
alert parser, first preview the generated commands. Execution is opt-in and
the continuous watcher must be stopped before it writes to the device:

```sh
python3 tools/stackchan_match_replay.py --config config/kalshi_watchlist.json
python3 tools/stackchan_match_replay.py --config config/kalshi_watchlist.json --language en
python3 tools/stackchan_match_replay.py --config config/kalshi_watchlist.json --execute
```

The replay reconstructs the score at each commentary entry, waits for each
celebration/TTS cycle to finish, and centers the head with torque and light off
when it completes.

## Security notes

The HTTP API is unauthenticated and CORS-open by design — run it only on a
trusted LAN and do not port-forward it. The fallback AP (`StackChan-Matchday`
/ `stackchan`) appears only when the device has no Wi-Fi credentials
(configure Wi-Fi with the official
[web console](https://stack-chan.github.io/web/) over BLE, or change the
constants in `mod/state.js`).

## Credits & licenses

- [Stack-chan](https://github.com/stack-chan/stack-chan) by Shinya Ishikawa —
  Apache-2.0
- Flag PNGs derived from [flag-icons](https://github.com/lipis/flag-icons) —
  MIT (`mod/LICENSE-flag-icons.txt`)
- This repository — [MIT](LICENSE)
