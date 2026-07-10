# Stack-chan Matchday

[English](README.md) | [简体中文](README.zh-CN.md)

Stack-chan Matchday is a lightweight [Stack-chan](https://github.com/stack-chan/stack-chan)
mod and Python LAN watcher that together turn a CoreS3 robot into a World Cup
co-watching companion. It shows both teams' Kalshi advance-market
probabilities, follows ESPN scores and live commentary, reacts with speech,
balloons, lights, and safe head movements, and lets you choose the next match
from a phone.

> [!IMPORTANT]
> This is a read-only match companion. It does not trade, access a Kalshi
> account, or provide betting advice. `position_team` is a manually entered
> preference used only to choose the post-match reaction. Kalshi data comes
> from its public REST API; ESPN data comes from publicly reachable,
> undocumented endpoints that may change or lag behind the broadcast.

## Features

- A persistent two-team probability bar with flags and a bottom market ticker.
- Goal, card, substitution, close-miss, match-state, and final-result reactions.
- Three switchable commentary styles: casual co-watching, balanced narration,
  and professional play-by-play with ESPN-supplied detail.
- A phone setup page hosted by Stack-chan, with live Chinese/English switching.
- Double-tap head-touch and Power-button shortcuts for the setup QR code.
- Fixture discovery, adaptive polling, hot configuration reload, and quiet hours.
- Optional LAN TTS; visual feedback and tone patterns still work without it.
- Standalone mode for following up to four active markets without a fixture.

## In action

<table>
  <tr>
    <td colspan="2" align="center">
      <img src="docs/images/photos/matchday-in-action.jpg" alt="Stack-chan following a football match beside a laptop" width="900"><br>
      <sub>Watching together: Stack-chan follows the same match beside the screen.</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="68%">
      <img src="docs/images/photos/device-probability-bar.jpg" alt="Stack-chan showing Spain at 92, the other side at 8, and a Chinese market-move alert" width="560"><br>
      <sub>Live reaction: a 92–8 split and an on-screen market-move alert.</sub>
    </td>
    <td align="center" width="32%">
      <img src="docs/images/photos/phone-setup-en.png" alt="English Stack-chan Match Setup page showing language, commentary style, and upcoming matches" width="200"><br>
      <sub>English setup page for choosing language, commentary style, and a match.</sub>
    </td>
  </tr>
</table>

## System design

![Stack-chan Matchday system design](docs/images/system-design.png)

Kalshi and ESPN are read-only inputs to the Python watcher. The watcher sends
fixture options, display commands, and acknowledgements to the Matchday mod.
It also parses match events into shared facts and renders the selected
commentary style; the mod only forwards settings and plays the resulting
display, speech, and reaction commands. The upstream host firmware and TTS
module are not modified for commentary styling.
The phone talks to Stack-chan's own `/setup` page; the device stores a pending
selection, and the watcher validates it, atomically updates the local JSON
configuration, hot-reloads, and acknowledges the device. For speech,
Stack-chan requests `/say?text=...` from the optional LAN TTS server and
receives a 24 kHz mono 16-bit PCM WAV response.

The phone, watcher host, TTS server, and Stack-chan must be on the same trusted
LAN. The optional watcher-hosted `:8788/setup` page is a local admin fallback,
not the primary QR flow.

## Repository layout

- `mod/` — the device mod, split into small JS modules plus flag and QR assets.
- `host/` — a required CoreS3 partition patch, an optional CJK-font patch, and
  the font preparation helper. These are build/resource changes; the upstream
  runtime JS/C source remains unchanged.
- `tools/` — the watcher, local setup service, macOS TTS server, replay tool,
  serial helper, asset generator, and tests. The default HTTP workflow uses the
  Python standard library; serial transport additionally requires `pyserial`.
- `config/` — the example watcher configuration and flag-pack definition.
- `docs/` — the [commentary-styles PRD](docs/commentary-styles-prd.md) and
  version-specific upgrade notes, including the bilingual
  [Matchday MOD 1.5.0 notes](docs/releases/1.5.0.md) and
  [1.4.0 notes](docs/releases/1.4.0.md).

## Requirements

- A CoreS3-based Stack-chan with 16 MB flash and a USB data cable.
- Git, Python 3.10+, Node.js 20+ (Node.js 22 is the tested upstream version),
  npm, and `xz` on the build computer.
- Moddable SDK and ESP-IDF. The upstream `xs-dev` setup command below installs
  and checks them.
- A phone and watcher computer on the same trusted LAN as Stack-chan.
- `qrencode` only when generating the device-specific setup QR.
- macOS only for the included `say`-based TTS server. Other systems can run the
  watcher without speech or provide a compatible `/say` WAV service.

The commands below are written for macOS/Linux shells. Set two absolute paths
once and keep using them throughout the installation:

```sh
mkdir -p "$HOME/src"
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
export STACKCHAN_DIR="$HOME/src/stack-chan"
```

## Install

Already running an earlier Matchday release? For 1.4.0 commentary styles,
update this watcher checkout and reinstall the Matchday mod. You do not need
to rebuild or reflash the official host firmware or replace the TTS module;
see the [1.4.0 release notes](docs/releases/1.4.0.md).

### 1. Clone and prepare the upstream build environment

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

The pinned commit is the tested base for the patches in this repository. Do
not build until `npm run doctor` lists `esp32` as a supported target. See the
upstream [getting-started guide](https://github.com/stack-chan/stack-chan/blob/dev/v1.0/firmware/docs/getting-started.md)
if `xs-dev` reports a platform-specific prerequisite.

### 2. Patch, build, and flash the host once

Apply the partition patch on every new CoreS3 host checkout:

```sh
cd "$STACKCHAN_DIR"
git am "$MATCHDAY_DIR/host/patches/0001-Add-xs-mod-partition-for-M5StackChan-CoreS3.patch"
```

For Chinese labels and balloons, also apply the font patch and prepare a
CJK-capable TTF. Pure-English installations may skip these two commands and
set the watcher language to `en`:

```sh
git am "$MATCHDAY_DIR/host/patches/0002-Add-optional-StackChanCN-24-GB2312-font-resource.patch"
python3 "$MATCHDAY_DIR/host/prepare_cjk_font.py" "$STACKCHAN_DIR"
```

Build and flash the host:

```sh
cd "$STACKCHAN_DIR/firmware"
export PATH="$PWD/node_modules/.bin:$PATH"
mcconfig -d -m -p esp32:./platforms/m5stackchan_cores3 -t deploy \
  "$PWD/stackchan/manifest_m5stackchan_cores3.json"
```

See [host/README.md](host/README.md) for font selection and patch details.

### 3. Generate the QR, then build and install the mod

The QR image is a static asset compiled into the mod; it is not generated at
runtime. Use a stable DHCP reservation, IP address, or resolvable mDNS name,
and generate the QR before installing the mod. The UI reads the PNG's natural
dimensions; keep both edges at or below 168 px so the title and URL still fit:

```sh
export STACKCHAN_HOST=stackchan.local
qrencode -s 4 -m 1 -o "$MATCHDAY_DIR/mod/assets/setup/setup-qr.png" \
  "http://$STACKCHAN_HOST/setup"
file "$MATCHDAY_DIR/mod/assets/setup/setup-qr.png"
```

If `file` reports an edge larger than 168 px, regenerate with `-s 3`. Then
build and install the mod:

```sh
cd "$STACKCHAN_DIR/firmware"
npm run mod --target=esp32:./platforms/m5stackchan_cores3 -- -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
```

`-f rgb565be` is required on CoreS3; without it, flag colors are byte-swapped.

`npm run mod` installs over the xsbug debug protocol, which needs an xsbug
listener and can stall mid-write while the device is busy (a stalled install
invalidates the mod until reinstalled). The debugger-free alternative is to
build only, then write the archive straight into the `xs` partition:

```sh
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
python3 -m esptool --chip esp32s3 --before default-reset --after hard-reset \
  write-flash 0xDF0000 "$MODDABLE/build/bin/esp32/debug/mod/mod.xsa"
```

`0xDF0000` is the `xs` partition offset from the partition patch; esptool
verifies the write and the host mounts the archive on the reset that follows.
Verify the mod from the watcher computer:

```sh
curl "http://$STACKCHAN_HOST/health"
curl "http://$STACKCHAN_HOST/api/status"
```

### 4. Configure and start the watcher

```sh
cp "$MATCHDAY_DIR/config/kalshi_watchlist.example.json" \
  "$MATCHDAY_DIR/config/kalshi_watchlist.json"
```

Edit the copied file and check these values:

- `stackchan_host` matches `$STACKCHAN_HOST` or the device's LAN IP.
- `stackchan_transport` is `http`; the phone setup relay does not work over
  serial transport.
- `setup_server.enabled` is `true`.
- Port `8788` is free. Its default `127.0.0.1` binding keeps the optional local
  admin page on the watcher computer only.

Validate the JSON, then keep the watcher running:

```sh
python3 -m json.tool "$MATCHDAY_DIR/config/kalshi_watchlist.json"
python3 "$MATCHDAY_DIR/tools/stackchan_kalshi_watch.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --watch
```

The example `KXEXAMPLE-...` tickers are intentional placeholders. Until you
select a live match from the phone or replace them with real open tickers, the
watcher may report them as missing. `--dry-run` only suppresses device writes;
it still queries the public APIs and is not an offline installation check.

### 5. Optional: enable LAN speech

In a second terminal on macOS, re-export the repository path and start the
included server in the foreground so errors remain visible:

```sh
export MATCHDAY_DIR="$HOME/src/stackchan-matchday"
python3 "$MATCHDAY_DIR/tools/stackchan_tts_server.py" --host 0.0.0.0 --port 8787
```

Leave that terminal running. In another terminal, verify it and point the
device at the watcher's LAN address, not `127.0.0.1`:

```sh
curl "http://127.0.0.1:8787/health"
export STACKCHAN_HOST=stackchan.local
export WATCHER_HOST=192.168.1.20
curl --request POST --data-binary "tts host $WATCHER_HOST:8787" \
  "http://$STACKCHAN_HOST/api/command"
curl --request POST --data-binary "say Matchday ready" \
  "http://$STACKCHAN_HOST/api/command"
```

Allow inbound TCP `8787` through the computer firewall. Use `say -v '?'` to
list installed macOS voices; override defaults with
`STACKCHAN_TTS_ZH_VOICE`, `STACKCHAN_TTS_EN_VOICE`, and
`STACKCHAN_TTS_RATE`. Without a reachable TTS server, the mod automatically
falls back to short tone patterns.

## How to use

1. **Start the watcher.** Keep the watcher computer awake and the `--watch`
   process running. The phone, computer, and Stack-chan must share a trusted
   LAN.
2. **Wake the setup QR.** Double-tap the three-zone touch bar on top of
   Stack-chan's head. Tap once while the QR is visible to hide it; it also
   closes after 90 seconds. A short Power-button press toggles it on the pinned
   host firmware.
3. **Scan and choose.** Open the QR on a phone, choose 中文 or English, select a
   match, your team (or Neutral), an optional pregame position (or No
   position), and a commentary style, then tap **Start watching**. The
   position is only a manual final result reaction preference; no account is
   read.
4. **Wait for confirmation.** The page first says that it is waiting for the
   watcher. The watcher validates the ESPN/Kalshi pairing, atomically updates
   the local configuration, hot-reloads, and acknowledges the device. No
   watcher or device restart is needed.
5. **Watch together.** During the match, Stack-chan updates flags and
   probabilities and reacts to score and commentary events with its screen,
   face, lights, head, tones, and optional speech.
6. **Watch any market.** If no fixture is available, paste a Kalshi event URL
   or ticker into the same page. The event's four most-traded markets appear in
   the bottom ticker; fixture-only flags, probability bar, and ESPN commentary
   are temporarily disabled.
7. **Mute for meetings (boss key).** Hold the top touch bar for about one
   second to toggle mute: speech, tones, celebrations, and alert lights stop,
   while the probability bar, balloons, and ticker keep updating silently. A
   corner `MUTE` / `静音` badge stays visible until unmuted. `mute on 60` over
   `/api/command` (or the control panel's *mute 60m* button) silences a timed
   meeting and announces when sound returns; an indefinite mute survives
   reboots.

<table>
  <tr>
    <td align="center" width="38%">
      <img src="docs/images/photos/scan-setup-qr.jpg" alt="A phone camera scanning the setup QR code displayed on Stack-chan" width="220"><br>
      <sub>Scan the on-device QR code to open the local setup page (example LAN address shown).</sub>
    </td>
    <td align="center" width="62%">
      <img src="docs/images/photos/head-touch-mute.jpg" alt="A hand holding Stack-chan's top touch bar while the device displays the Chinese mute confirmation" width="380"><br>
      <sub>Hold the top touch bar to mute speech, tones, motion, and alert lights while visual updates continue.</sub>
    </td>
  </tr>
</table>

Once per local day, the watcher can proactively ask you to scan and choose a
match. Configure this with `setup_server.daily_prompt_hour` (`-1` disables it),
`prompt_minutes_before`, `quiet_hours`, and `lookahead_days`.

## Configuration and language

The setup page switches all labels immediately and persists its language on
the device. Applying a match also switches watcher-generated speech and
balloons. The top-level `language` value may be `zh` or `en`.

User-facing text accepts either a legacy string or a localized object:

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

The same localized leaf format works for `player_names`, `star_chants`, and
custom goal-signal speech. Missing English names fall back to ESPN's source
name. A legacy string is used verbatim in both modes.

### Commentary styles

`espn.commentary_style` is a persistent global preference and accepts exactly
`casual`, `balanced`, or `professional`. Configurations that omit it continue
to use `balanced`.

| Value | Voice | Device balloon |
| --- | --- | --- |
| `casual` | Friendly co-watching language, while retaining every core fact | Compact event summary |
| `balanced` | Clear, natural narration compatible with the previous behavior | Compact event summary |
| `professional` | Core facts plus reliably parsed ESPN details and football terminology | Compact event summary; detail stays in speech |

Every style keeps the match time, event type, team, required players, event
result, and current score. Penalties, cards, and substitutions keep all
necessary participants. Suspected goals and events awaiting commentary
confirmation remain explicitly uncertain in every style. Professional speech
may add an assist or cross, shot type and body part, field or goal location,
goalkeeper save, set-piece position, or substitution/injury reason only when
ESPN explicitly supplies it and the watcher can parse it reliably; it never
reads the raw English commentary aloud or guesses missing detail.

Across all three styles, balloons normally stay in the form “time +
player/team + event + score”; only punctuation and a small amount of wording
vary.

The selected style also applies to match phases and results, Kalshi market
jumps, and suspected-goal alerts. It changes wording only: TTS voice and rate,
sound effects, celebrations, expressions, lights, priorities, and alert
switches are unchanged. You can switch it from either setup page during a
match; newly generated alerts use the new style immediately without replaying
old ESPN events or resetting market baselines, queues, or polling state.

Serial transport is intended for direct command/control only. Install
`pyserial` and configure `stackchan_serial_port` if you choose it; phone setup,
device status detection, and the options/pending/ack relay require HTTP.

## Troubleshooting

- **The setup page has no matches:** confirm the watcher is running with setup
  enabled and can reach both public APIs. Only open Kalshi events in the
  configured `kalshi_series_ticker` that match an ESPN `pre` or `in` fixture
  by both team names are listed.
- **The page stays on “waiting for watcher”:** the watcher is stopped, is using
  serial transport, failed to bind local port `8788`, or cannot reach the
  device on TCP `80`. Check its terminal output first.
- **The QR opens the wrong address:** regenerate
  `mod/assets/setup/setup-qr.png` and reinstall the mod. The bitmap is static;
  changing the on-screen URL does not rewrite its modules.
- **Chinese renders as boxes:** apply the optional font patch, prepare a CJK
  TTF, then rebuild and reflash the host before reinstalling the mod.
- **There is no speech:** first look for the corner `MUTE` / `静音` badge or
  run `mute status` — a long-press on the top bar toggles the boss key. Then
  check the TTS `/health` response, computer firewall, and `tts status`
  through `/api/command`. Visual effects and tone fallback do not depend on
  TTS.
- **`npm run mod` hangs at "Installing mod...":** the xsbug-protocol install
  stalled; a killed install leaves the mod missing until reinstalled. Quit
  xsbug, then use the build + `esptool write-flash 0xDF0000` path from the
  install section — it needs no debugger and verifies the write.
- **Device freezes and drops off the network while xsbug is attached:** xsbug
  pauses the whole runtime at exception breakpoints, stopping Wi-Fi, touch,
  and timers. Detach `serial2xsbug`/xsbug for unattended running; to capture
  logs without freezing, use `$MODDABLE/tools/xsbug-log` instead.
- **`stackchan.local` does not resolve:** use the device's LAN IP and regenerate
  the QR. Reserve that address in DHCP so the static QR stays valid.
- **Markets show as missing:** the example values are placeholders. Select a
  live fixture from the setup page or replace them with open tickers returned
  by `python3 "$MATCHDAY_DIR/tools/stackchan_kalshi_watch.py" discover --query QUERY`.

## Device API

`GET /api/help` lists the plain-text command surface accepted by
`POST /api/command`:

```text
pkbar es 62 AA151B be 38 EF3340
balloon temp 8000 Spain scores!
voice favorite-goal Number seven scores!
celebrate goal 170 21 27
celebrate say 170 21 27 Goal!
celebrate result win 170 21 27 Full time
setup show http://stackchan.local/setup
say Hello
mute on 60 · mute off · mute status
face happy · look 8 -2 · idle look on · light flash 0 85 164
```

`GET /api/status` reports the mod version, probability bar, TTS, power,
network, and setup-trigger counters. `POST /api/control` accepts JSON actions.
The watcher-facing setup endpoints are `/api/match-setup`, `/options`,
`/apply`, `/ack`, `/pending`, and `/language`.

Commentary style has a dedicated relay path. The device accepts
`POST /api/match-setup/style` with
`{"commentary_style":"casual|balanced|professional"}` and forwards it through
the existing pending/ack flow. The watcher-hosted admin service accepts the
same body at `POST /api/setup/style`; `GET /api/setup/status` reports the
effective value.

## Development

Run the complete local test suite from this repository:

```sh
cd "$MATCHDAY_DIR"
python3 -m unittest discover -s tools -p 'test_*.py'
node tools/test_stackchan_mod_web_behavior.mjs
```

Build a mod archive without installing it from the upstream `firmware/`
directory:

```sh
cd "$STACKCHAN_DIR/firmware"
mcrun -d -m -p esp32:./platforms/m5stackchan_cores3 -t build -f rgb565be \
  "$MATCHDAY_DIR/mod/manifest.json"
```

Replay the France–Morocco ESPN history through the same alert parser. Preview
is the default; stop the continuous watcher before opting into execution:

```sh
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json"
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --language en
python3 "$MATCHDAY_DIR/tools/stackchan_match_replay.py" \
  --config "$MATCHDAY_DIR/config/kalshi_watchlist.json" --execute
```

## Security

The device HTTP API is unauthenticated and CORS-open by design. Use it only on
a trusted LAN; do not port-forward TCP `80`, `8787`, or `8788`. The fallback AP
(`StackChan-Matchday` / `stackchan`) appears only when the device has no Wi-Fi
credentials. Configure Wi-Fi with the official
[Stack-chan web console](https://stack-chan.github.io/web/) over BLE before
starting the watcher.

## Credits and licenses

- [Stack-chan](https://github.com/stack-chan/stack-chan) by Shinya Ishikawa —
  Apache-2.0.
- Flag PNGs derived from [flag-icons](https://github.com/lipis/flag-icons) —
  MIT; see `mod/LICENSE-flag-icons.txt`.
- This repository — [MIT](LICENSE).
