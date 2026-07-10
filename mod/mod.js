// Stack-chan Matchday MOD entry point.
//
// A lightweight companion for watching a match with Kalshi advance-market
// odds: persistent two-team probability bar, live-commentary balloons and
// tones, goal / final-result celebrations, and a phone setup page. Pairs with
// the LAN watcher in tools/stackchan_kalshi_watch.py.
//
// Designed to run on a stock stack-chan host (dev/v1.0): no host patches, no
// custom partition table. If the host bundles the optional StackChanCN-24
// font (see host/cjk-font/), balloons and labels render Chinese; otherwise
// they fall back to the stock font.
import WiFi from 'wifi'
import {
  AP_PASSWORD,
  AP_SSID,
  MOD_VERSION,
  clamp,
  hasUsableIp,
  nowTicks,
  readPreference,
  safeNetGet,
  state,
  toNumber,
  MIN_IDLE_LOOK_INTERVAL_MS,
  MAX_IDLE_LOOK_INTERVAL_MS,
} from 'matchday/state'
import {
  noteActivity,
  setScreenBrightness,
  showBalloon,
  startIdleLook,
  startPowerManager,
  temporaryBalloon,
} from 'matchday/ui'
import { startHttp } from 'matchday/web'

function restoreSettings(robot) {
  try {
    state.power.autoDim = Boolean(readPreference('autoDim', state.power.autoDim))
    state.power.idleMs = Math.round(
      clamp(toNumber(readPreference('pIdleMs', state.power.idleMs), state.power.idleMs), 10000, 600000),
    )
    state.power.dimBrightness = Math.round(
      clamp(toNumber(readPreference('dimBright', state.power.dimBrightness), state.power.dimBrightness), 0, 100),
    )
    state.power.wakeBrightness = Math.round(
      clamp(toNumber(readPreference('wakeBright', state.power.wakeBrightness), state.power.wakeBrightness), 0, 100),
    )
    setScreenBrightness(state.power.wakeBrightness)

    state.idle.intervalMs = Math.round(
      clamp(
        toNumber(readPreference('idleMs', state.idle.intervalMs), state.idle.intervalMs),
        MIN_IDLE_LOOK_INTERVAL_MS,
        MAX_IDLE_LOOK_INTERVAL_MS,
      ),
    )
    if (readPreference('idleLook', false) === true) {
      startIdleLook(robot, state.idle.intervalMs)
    }

    const pendingSetup = String(readPreference('matchSetupPending', ''))
    if (pendingSetup) {
      state.matchSetup.pending = JSON.parse(pendingSetup)
    }
    trace('[matchday] settings restored\n')
  } catch (error) {
    trace(`[matchday] settings restore failed ${error}\n`)
  }
}

function startFallbackAccessPoint(robot) {
  const currentIp = safeNetGet('IP')
  if (hasUsableIp(currentIp)) {
    trace(`[matchday] network IP ${currentIp}; fallback AP disabled\n`)
    return
  }

  try {
    trace(`[matchday] no usable IP (${currentIp || 'none'}); starting fallback AP\n`)
    WiFi.accessPoint({
      ssid: AP_SSID,
      password: AP_PASSWORD,
      channel: 8,
      hidden: false,
    })
    const apIp = safeNetGet('IP')
    state.accessPoint = {
      ssid: AP_SSID,
      password: AP_PASSWORD,
      ip: hasUsableIp(apIp) ? apIp : '192.168.4.1',
    }
    showBalloon(robot, `${AP_SSID} ${state.accessPoint.ip}`)
    trace(`[matchday] fallback AP ${AP_SSID} password=${AP_PASSWORD}\n`)
  } catch (error) {
    state.lastError = `fallback ap failed: ${error}`
    trace(`[matchday] fallback AP failed ${error}\n`)
  }
}

let httpServer

export function onRobotCreated(robot, _device) {
  trace(`[matchday] starting v${MOD_VERSION}\n`)
  state.diagnostics.startedTicks = nowTicks()
  noteActivity('boot')
  restoreSettings(robot)
  temporaryBalloon(robot, `matchday v${MOD_VERSION}`, 2200)
  startPowerManager()
  startFallbackAccessPoint(robot)
  httpServer = startHttp(robot)
}
