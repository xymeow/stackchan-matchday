// Stack-chan Matchday MOD entry point.
//
// A lightweight companion for watching a match with Kalshi advance-market
// odds: persistent two-team probability bar, live-commentary balloons and
// tones, goal / final-result celebrations, and a phone setup page. Pairs with
// the LAN watcher in tools/stackchan_kalshi_watch.py.
//
// Designed for the official stack-chan host (dev/v1.0). CoreS3 needs the
// build-level mod-partition patch in host/; runtime JS/C stays unchanged. If
// the host also bundles the optional StackChanCN-24 resource, balloons and
// labels render Chinese; otherwise they fall back to the stock font.
import WiFi from 'wifi'
import {
  AP_PASSWORD,
  AP_SSID,
  COMMENTARY_STYLES,
  MOD_VERSION,
  clamp,
  hasUsableIp,
  nowTicks,
  readPreference,
  safeNetGet,
  savePreference,
  state,
  toNumber,
  MIN_IDLE_LOOK_INTERVAL_MS,
  MAX_IDLE_LOOK_INTERVAL_MS,
} from 'matchday/state'
import {
  hideSetupQr,
  noteActivity,
  setMuteBadge,
  setScreenBrightness,
  showBalloon,
  showSetupQr,
  startIdleLook,
  startPowerManager,
  temporaryBalloon,
} from 'matchday/ui'
import { runCommand } from 'matchday/commands'
import { startHttp } from 'matchday/web'

// Release hosts restart on uncaught exceptions with no console trace left
// behind (fxAbort -> esp_restart). With the host's MODDEF_XS_ABORTHOOK, this
// hook runs first: record why we died so /api/status can report it after the
// reboot. Returning false lets the restart proceed.
globalThis.abort = function (status, exception) {
  try {
    const reason = `${status ?? 'abort'}: ${exception ?? ''}`.slice(0, 160)
    savePreference('lastAbort', reason)
  } catch (_error) {
    // never throw from the abort hook
  }
  return false
}

const SETUP_POWER_DEBOUNCE_MS = 600
const SETUP_TOP_TAP_MAX_MS = 500
const SETUP_TOP_MIN_INTER_TAP_MS = 120
const SETUP_TOP_DOUBLE_TAP_WINDOW_MS = 900
const SETUP_TOP_COOLDOWN_MS = 1500
// The host GestureRecognizer counts any Si12T zone intensity >= 1 as a touch
// and that threshold is fixed at construction, so on a warm device the
// recognizer can sit in its "touched" state forever and never emit release —
// taps stop arriving entirely. The mod therefore reads the raw 0-3 intensity
// samples itself with hysteresis: a press needs a firm >= ON reading and a
// release is any dip to <= OFF, so baseline drift at 1 neither triggers nor
// wedges the detector.
const SETUP_TOP_TOUCH_ON = 2
const SETUP_TOP_TOUCH_OFF = 1
const SETUP_TOP_SWIPE_REJECT = 45
// Boss key: hold the top bar this long to toggle mute without the phone.
const MUTE_LONG_PRESS_MS = 1100

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

    const savedLanguage = String(readPreference('language', ''))
    if (savedLanguage === 'zh' || savedLanguage === 'en') {
      state.matchSetup.language = savedLanguage
    }

    const savedCommentaryStyle = String(readPreference('commentaryStyle', 'balanced'))
      .trim()
      .toLowerCase()
    if (COMMENTARY_STYLES.includes(savedCommentaryStyle)) {
      state.matchSetup.commentaryStyle = savedCommentaryStyle
    }

    if (readPreference('muted', false) === true) {
      state.mute.on = true
    }

    const pendingSetup = String(readPreference('matchSetupPending', ''))
    if (pendingSetup) {
      const pending = JSON.parse(pendingSetup)
      state.matchSetup.pending = pending
      if (pending?.language === 'zh' || pending?.language === 'en') {
        state.matchSetup.language = pending.language
      }
    }

    const lastAbort = String(readPreference('lastAbort', ''))
    if (lastAbort) {
      state.diagnostics.lastAbort = lastAbort
      trace(`[matchday] previous run aborted: ${lastAbort}\n`)
      // Consume the record so /api/status only ever reports an abort of the
      // run immediately before this boot, not one from days ago.
      savePreference('lastAbort', undefined)
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

function localSetupUrl() {
  const networkIp = safeNetGet('IP')
  const ip = hasUsableIp(networkIp) ? networkIp : state.accessPoint?.ip
  return hasUsableIp(ip) ? `http://${ip}/setup` : ''
}

function toggleLocalSetup(robot, source, ticks = nowTicks()) {
  noteActivity(`setup-trigger:${source}`)
  state.setup.trigger.lastSource = source
  state.setup.trigger.lastTriggeredTicks = ticks
  if (state.setup.visible) {
    hideSetupQr(robot)
    return
  }

  const url = localSetupUrl()
  if (!url) {
    temporaryBalloon(robot, 'Setup unavailable: no network', 2600)
    return
  }
  const result = showSetupQr(robot, url)
  if (!result.ok) state.lastError = result.text.trim()
}

function configureSetupTriggers(robot) {
  const sources = []
  const powerButton = globalThis.button?.power
  if (powerButton) {
    sources.push('power-button')
    let lastPowerTicks = -Infinity
    const previousPowerHandler = powerButton.onChanged
    powerButton.onChanged = function () {
      previousPowerHandler?.call(this)
      if (!this.read()) return
      const ticks = nowTicks()
      if (ticks - lastPowerTicks < SETUP_POWER_DEBOUNCE_MS) return
      lastPowerTicks = ticks
      toggleLocalSetup(robot, 'power-button', ticks)
    }
  }

  const touchPanel = robot.touchPanel
  if (touchPanel) {
    sources.push('top-double-tap')
    const touchState = state.setup.trigger.touch
    const previousOnSample = touchPanel.onSample
    let touchActive = false
    let touchStartTicks = 0
    let touchStartPosition = 0
    let touchMaxDrift = 0
    let longPressFired = false
    let firstTapTicks
    let ignoreUntilTicks = 0

    // Same left/center/right weighting the host recognizer uses: -100..100.
    const topPosition = (sample) => {
      const left = sample[0] ?? 0
      const center = sample[1] ?? 0
      const right = sample[2] ?? 0
      const total = left + center + right
      if (!total) return 0
      return Math.trunc((left * -100 + center * 0 + right * 100) / total)
    }

    const handleTap = (ticks) => {
      if (ticks < ignoreUntilTicks) {
        firstTapTicks = undefined
        return
      }
      if (state.setup.visible) {
        firstTapTicks = undefined
        ignoreUntilTicks = ticks + SETUP_TOP_COOLDOWN_MS
        toggleLocalSetup(robot, 'top-double-tap', ticks)
        return
      }
      if (firstTapTicks !== undefined) {
        const gap = ticks - firstTapTicks
        if (gap < SETUP_TOP_MIN_INTER_TAP_MS) return
        if (gap <= SETUP_TOP_DOUBLE_TAP_WINDOW_MS) {
          firstTapTicks = undefined
          ignoreUntilTicks = ticks + SETUP_TOP_COOLDOWN_MS
          toggleLocalSetup(robot, 'top-double-tap', ticks)
          return
        }
      }
      firstTapTicks = ticks
    }

    touchPanel.onSample = function (sample, ticks) {
      previousOnSample?.call(this, sample, ticks)
      // An exception here would kill the panel's sampling timer and freeze
      // every touch feature until reboot, so the handler never throws.
      try {
        if (ticks === undefined) ticks = nowTicks()
        const intensity = Math.max(sample?.[0] ?? 0, sample?.[1] ?? 0, sample?.[2] ?? 0)
        touchState.intensity = intensity
        touchState.position = topPosition(sample ?? [])

        if (!touchActive) {
          if (intensity >= SETUP_TOP_TOUCH_ON) {
            touchActive = true
            touchState.active = true
            touchStartTicks = ticks
            touchStartPosition = touchState.position
            touchMaxDrift = 0
            longPressFired = false
          }
          return
        }

        if (intensity > SETUP_TOP_TOUCH_OFF) {
          const drift = Math.abs(touchState.position - touchStartPosition)
          if (drift > touchMaxDrift) touchMaxDrift = drift
          // Boss key: a steady long hold toggles mute while still pressed, so
          // the user gets feedback (balloon + badge) before letting go.
          if (
            !longPressFired &&
            ticks - touchStartTicks >= MUTE_LONG_PRESS_MS &&
            touchMaxDrift <= SETUP_TOP_SWIPE_REJECT
          ) {
            longPressFired = true
            noteActivity('setup-trigger:top-long-press')
            state.setup.trigger.lastSource = 'top-long-press'
            state.setup.trigger.lastTriggeredTicks = ticks
            runCommand(robot, state.mute.on ? 'mute off' : 'mute on').catch((error) => {
              state.lastError = `mute toggle: ${error}`
            })
          }
          return
        }

        touchActive = false
        touchState.active = false
        const heldMs = ticks - touchStartTicks
        touchState.lastTapMs = heldMs
        touchState.lastDrift = touchMaxDrift
        if (longPressFired) {
          // The hold already toggled mute; its release is not a tap.
          longPressFired = false
          firstTapTicks = undefined
          return
        }
        const isTap = heldMs <= SETUP_TOP_TAP_MAX_MS && touchMaxDrift <= SETUP_TOP_SWIPE_REJECT
        if (!isTap) {
          firstTapTicks = undefined
          return
        }
        touchState.taps += 1
        handleTap(ticks)
      } catch (error) {
        state.lastError = `setup-trigger touch: ${error}`
      }
    }
  }

  state.setup.trigger.available = sources.length > 0
  state.setup.trigger.sources = sources
  trace(`[matchday] setup triggers: ${sources.join(', ') || 'none'}\n`)
}

let httpServer

export function onRobotCreated(robot, _device) {
  trace(`[matchday] starting v${MOD_VERSION}\n`)
  state.diagnostics.startedTicks = nowTicks()
  noteActivity('boot')
  restoreSettings(robot)
  if (state.mute.on) setMuteBadge(robot, true)
  temporaryBalloon(robot, `matchday v${MOD_VERSION}`, 2200)
  startPowerManager()
  startFallbackAccessPoint(robot)
  httpServer = startHttp(robot)
  configureSetupTriggers(robot)
}
