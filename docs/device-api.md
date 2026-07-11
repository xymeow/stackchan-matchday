# Device API

[English](device-api.md) | [简体中文](device-api.zh-CN.md)

Stack-chan Matchday exposes an unauthenticated, CORS-open HTTP API for trusted
LAN use. Do not expose device TCP `80`, TTS TCP `8787`, or watcher setup TCP
`8788` to the internet.

## Health, help, and status

- `GET /health` checks that the device web service is reachable.
- `GET /api/help` returns the plain-text command surface accepted by
  `POST /api/command`.
- `GET /api/status` reports the mod version, probability bar, TTS, power,
  network, mute state, and setup-trigger counters.

## Plain-text commands

Send one command as the raw body of `POST /api/command`:

```text
pkbar es 62 AA151B be 38 EF3340
balloon temp 8000 Spain scores!
voice favorite-goal Number seven scores!
celebrate goal 170 21 27
celebrate say 170 21 27 Goal!
celebrate result win 170 21 27 Full time
setup show http://stackchan.local/setup
say Hello
mute on 60
mute off
mute status
face happy
look 8 -2
idle look on
light flash 0 85 164
```

For example:

```sh
export STACKCHAN_HOST=stackchan.local
curl --request POST --data-binary "say Matchday ready" \
  "http://$STACKCHAN_HOST/api/command"
curl --request POST --data-binary "mute status" \
  "http://$STACKCHAN_HOST/api/command"
```

## JSON control

`POST /api/control` accepts JSON actions for the browser-based control panel
and other structured clients. Device capabilities may vary by mod version, so
clients should check the HTTP response and, when needed, read `/api/status`
instead of assuming that an action ran.

```sh
curl --request POST \
  --header 'Content-Type: application/json' \
  --data '{"action":"mute","enabled":true,"minutes":60}' \
  "http://$STACKCHAN_HOST/api/control"
```

Use `GET /api/help` on the installed mod as the runtime source of truth for
available plain-text commands.

## Match Setup relay

The device-facing setup surface uses these endpoints under `/api/match-setup`:

- `/options` receives fixture and standalone-event choices from the watcher.
- `/apply` stores a phone selection as pending.
- `/pending` lets the watcher retrieve that selection.
- `/ack` confirms success or reports validation failure back to the phone.
- `/language` updates the persisted device language.

These endpoints form a pending/ack handshake: the phone writes to Stack-chan,
the watcher validates and atomically updates its local configuration, and the
device displays the acknowledgement. They require HTTP transport.

## Commentary-style endpoints

The device accepts `POST /api/match-setup/style` with one of `casual`,
`balanced`, or `professional`, for example:

```json
{"commentary_style":"professional"}
```

It forwards the setting through the same pending/ack flow. The watcher-hosted
admin service accepts the same body at `POST /api/setup/style`.
`GET /api/setup/status` reports the effective value.

Changing only the style must not reset ESPN event history, market baselines,
alert queues, or polling state, and must not replay old commentary. The new
style applies to subsequently generated alerts.

## Calling conventions

- Use every endpoint only on a trusted LAN; the device API has no
  authentication and allows CORS.
- Send text commands as UTF-8. Preserve Chinese text rather than translating
  it before sending it to the device.
- Watcher HTTP delivery should detect failures and back off instead of retrying
  continuously; device resources are limited.
- Avoid high-frequency status polling while long TTS audio is playing.
- Match Setup and its pending/ack flow require HTTP; serial transport does not
  provide this workflow.
- When changing an endpoint, update the mod, watcher client, tests, and this
  guide together.
