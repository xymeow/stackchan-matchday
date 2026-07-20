// Text command dispatcher shared by HTTP /api/command and /api/control.
// The stable command strings let watcher and device tooling evolve separately.
import Modules from 'modules'
import Timer from 'timer'

// Optional: only present when the host bundles modules/base/instrumentation.
// Exposes XS heap usage in /api/status so heap exhaustion (the 2026-07-15
// "Chunk allocation failed" reboot signature) can be graphed from outside.
let Instrumentation
try {
  Instrumentation = Modules.importNow('instrumentation')
} catch (_error) {}

function instrumentsPayload() {
  if (!Instrumentation) return undefined
  const values = {}
  try {
    for (let index = 1; index < 40; index++) {
      const name = Instrumentation.name(index)
      if (!name) break
      values[name] = Instrumentation.get(index)
    }
  } catch (_error) {}
  return values
}
import {
  EMOTIONS,
  MOD_NAME,
  MOD_VERSION,
  clamp,
  elapsedSince,
  networkPayload,
  normalizeEmotion,
  nowTicks,
  parseNumbers,
  savePreference,
  state,
  toNumber,
  toRadians,
  screenLookToVector,
} from 'matchday/state'
import {
  FAN_CLIP_IDS,
  blinkHeadLight,
  celebrateGoal,
  celebrateResult,
  executeTtsCommand,
  playFanClip,
  playSpeechOrClip,
  speakRemote,
  ttsPayload,
} from 'matchday/audio'
import {
  cancelBalloonAutoHide,
  hideBalloon,
  hideProbabilityBar,
  hideSetupQr,
  noteActivity,
  scheduleBalloonAutoHide,
  setMuteBadge,
  setProbabilityBar,
  setScreenBrightness,
  setTicker,
  setUserBrightness,
  showBalloon,
  showSetupQr,
  startIdleLook,
  stopIdleLook,
  temporaryBalloon,
} from 'matchday/ui'

// ---------------------------------------------------------------------------
// Boss key: one gesture/command silences speech, tones, celebrations, and
// alert lights. Indefinite mute persists across reboots; timed mute does not.

let muteTimer

export function setMute(robot, on, minutes = 0) {
  if (muteTimer !== undefined) {
    Timer.clear(muteTimer)
    muteTimer = undefined
  }
  state.mute.on = on
  state.mute.untilTicks = 0
  if (on && minutes > 0) {
    const durationMs = Math.round(minutes * 60000)
    state.mute.untilTicks = nowTicks() + durationMs
    muteTimer = Timer.set(() => {
      muteTimer = undefined
      setMute(robot, false)
      temporaryBalloon(
        robot,
        state.matchSetup.language === 'en' ? 'Sound is back on' : '静音结束，声音已恢复',
        2600,
      )
    }, durationMs)
    savePreference('muted', undefined)
  } else {
    savePreference('muted', on ? true : undefined)
  }
  setMuteBadge(robot, on)
}

function executeMuteCommand(robot, muteCommand) {
  const parts = muteCommand.trim().split(/\s+/).filter(Boolean)
  const action = parts[0]?.toLowerCase()
  const english = state.matchSetup.language === 'en'
  if (!action || action === 'status') {
    const remainingMs = state.mute.untilTicks ? Math.max(0, state.mute.untilTicks - nowTicks()) : 0
    const remaining = remainingMs ? ` (${Math.ceil(remainingMs / 60000)}m left)` : ''
    return { ok: true, text: `mute ${state.mute.on ? 'on' : 'off'}${remaining}\n` }
  }
  if (action === 'on' || action === 'true' || action === '1') {
    const minutes = Math.round(clamp(toNumber(parts[1], 0), 0, 720))
    setMute(robot, true, minutes)
    temporaryBalloon(
      robot,
      english
        ? minutes
          ? `Muted for ${minutes} min`
          : 'Muted - long-press the top bar to unmute'
        : minutes
          ? `已静音 ${minutes} 分钟`
          : '已静音，长按头顶恢复',
      2600,
    )
    return { ok: true, text: `ok mute on${minutes ? ` ${minutes}m` : ''}\n` }
  }
  if (action === 'off' || action === 'false' || action === '0') {
    setMute(robot, false)
    temporaryBalloon(robot, english ? 'Sound is back on' : '声音已恢复', 2200)
    return { ok: true, text: 'ok mute off\n' }
  }
  return { ok: false, text: 'error usage: mute on [minutes] | mute off | mute status\n' }
}

const COMMAND_USAGE = [
  'help',
  'status',
  'diag',
  'face neutral|happy|sad|angry|sleep|surprise|cold|hot',
  'emotion NEUTRAL|HAPPY|SLEEPY|DOUBTFUL|SAD|ANGRY|COLD|HOT',
  'look <screen-x:-12..12> <screen-y:-8..8>',
  'look3 <x-meters> <y-meters> <z-meters>',
  'look away',
  'idle look on|off [interval-ms]',
  'say <text>',
  'balloon <text>',
  'balloon temp <milliseconds> <text>',
  'balloon off',
  'ticker <text>',
  'ticker off',
  'pkbar <left-flag> <left-%> <left-hex> <right-flag> <right-%> <right-hex>',
  'pkbar off|status',
  'clip <id>|list',
  'voice <fallback-clip-id> <text>',
  'celebrate goal <r> <g> <b>',
  'celebrate result win|lose <r> <g> <b> [text]',
  'celebrate say <r> <g> <b> <text>',
  'pose <yaw-deg> <pitch-deg> [roll-deg] [seconds]',
  'servo <pan:0..180> <tilt:0..180>',
  'torque on|off',
  'mouth <0..1>',
  'color primary|secondary <r> <g> <b>',
  'light on <r> <g> <b> [led-name]',
  'light blink <r> <g> <b> [ms] [led-name]',
  'light flash <r> <g> <b> [duration-ms] [interval-ms]',
  'light rainbow [led-name]',
  'light off [led-name]',
  'tone <hz> <ms> [volume:0..1]',
  'mute on [minutes]',
  'mute off',
  'mute status',
  'tts status',
  'tts host <ip[:port]>',
  'tts host clear',
  'setup show <http-url>',
  'setup hide|status',
  'screen brightness <0..100>',
  'brightness <0..100>',
  'screen sleep|wake',
  'power auto on|off',
  'power idle <ms>',
  'power dim <0..100>',
  'power wake <0..100>',
]

export function helpText() {
  return `${MOD_NAME} commands:\n${COMMAND_USAGE.map((line) => `  ${line}`).join('\n')}\n`
}

export function statusPayload() {
  return {
    ok: true,
    runtime: 'official-stack-chan',
    mod: MOD_NAME,
    version: MOD_VERSION,
    uptimeMs: elapsedSince(state.diagnostics.startedTicks),
    emotion: state.emotion,
    speech: state.speech,
    balloon: state.balloon,
    ticker: state.ticker,
    probabilityBar: state.probabilityBar,
    setup: state.setup,
    celebrating: state.celebrating,
    mute: {
      on: state.mute.on,
      remainingMs: state.mute.untilTicks ? Math.max(0, state.mute.untilTicks - nowTicks()) : 0,
    },
    fanClips: FAN_CLIP_IDS,
    gaze: state.gaze,
    pose: state.pose,
    torque: state.torque,
    mouth: state.mouth,
    light: state.light,
    idle: state.idle,
    screen: state.screen,
    tts: ttsPayload(),
    power: {
      autoDim: state.power.autoDim,
      idleMs: state.power.idleMs,
      dimBrightness: state.power.dimBrightness,
      wakeBrightness: state.power.wakeBrightness,
      dimmed: state.power.dimmed,
      idleForMs: elapsedSince(state.power.lastActivityTicks),
      lastActivity: state.power.lastActivity,
    },
    ports: { http: 80 },
    network: networkPayload(),
    lastCommand: state.lastCommand,
    lastError: state.lastError,
    lastAbort: state.diagnostics.lastAbort ?? '',
    instruments: instrumentsPayload(),
  }
}

function diagnosticsPayload(robot) {
  return {
    ok: true,
    runtime: 'official-stack-chan',
    mod: MOD_NAME,
    version: MOD_VERSION,
    uptimeMs: elapsedSince(state.diagnostics.startedTicks),
    hardware: {
      touchScreen: Boolean(robot.touch),
      topTouchPanel: Boolean(robot.touchPanel),
      microphone: Boolean(robot.microphone),
      speaker: true,
      leds: Object.keys(robot.led ?? {}),
      power: Boolean(globalThis.power),
    },
    screen: state.screen,
    power: state.power,
    light: state.light,
    tts: ttsPayload(),
    network: networkPayload(),
    idle: state.idle,
    lastCommand: state.lastCommand,
    lastError: state.lastError,
  }
}

function executeIdleCommand(robot, idleCommand) {
  const parts = idleCommand.trim().split(/\s+/).filter(Boolean)
  if (parts[0]?.toLowerCase() === 'look') {
    parts.shift()
  }
  const action = parts[0]?.toLowerCase()
  if (!action || action === 'status') {
    return {
      ok: true,
      text: `idle look ${state.idle.look ? 'on' : 'off'} ${state.idle.intervalMs}\n`,
    }
  }
  if (action === 'on' || action === 'true' || action === '1') {
    const interval = Number.isFinite(Number(parts[1])) ? Number(parts[1]) : state.idle.intervalMs
    startIdleLook(robot, interval)
    savePreference('idleLook', true)
    savePreference('idleMs', state.idle.intervalMs)
    return { ok: true, text: `ok idle look on ${state.idle.intervalMs}\n` }
  }
  if (action === 'off' || action === 'false' || action === '0') {
    stopIdleLook(robot, true)
    savePreference('idleLook', false)
    return { ok: true, text: 'ok idle look off\n' }
  }
  return { ok: false, text: 'error usage: idle look on|off [interval-ms]\n' }
}

function executePowerCommand(robot, powerCommand) {
  const parts = powerCommand.trim().split(/\s+/).filter(Boolean)
  const action = parts[0]?.toLowerCase()
  if (!action || action === 'status') {
    return { ok: true, text: JSON.stringify(diagnosticsPayload(robot)) }
  }
  if (action === 'auto' || action === 'save') {
    const value = parts[1]?.toLowerCase()
    if (value === 'on' || value === 'true' || value === '1') state.power.autoDim = true
    else if (value === 'off' || value === 'false' || value === '0') state.power.autoDim = false
    else return { ok: false, text: 'error usage: power auto on|off\n' }
    if (!state.power.autoDim) state.power.dimmed = false
    savePreference('autoDim', state.power.autoDim)
    return { ok: true, text: `ok power auto ${state.power.autoDim ? 'on' : 'off'}\n` }
  }
  if (action === 'idle') {
    const idleMs = Math.round(clamp(toNumber(parts[1], state.power.idleMs), 10000, 600000))
    state.power.idleMs = idleMs
    savePreference('pIdleMs', idleMs)
    return { ok: true, text: `ok power idle ${idleMs}\n` }
  }
  if (action === 'dim') {
    state.power.dimBrightness = Math.round(clamp(toNumber(parts[1], state.power.dimBrightness), 0, 100))
    savePreference('dimBright', state.power.dimBrightness)
    return { ok: true, text: `ok power dim ${state.power.dimBrightness}\n` }
  }
  if (action === 'wake') {
    state.power.wakeBrightness = Math.round(clamp(toNumber(parts[1], state.power.wakeBrightness), 0, 100))
    savePreference('wakeBright', state.power.wakeBrightness)
    return { ok: true, text: `ok power wake ${state.power.wakeBrightness}\n` }
  }
  if (action === 'now' || action === 'sleep' || action === 'cool') {
    const result = setScreenBrightness(parts[1] ?? state.power.dimBrightness)
    if (result.ok) state.power.dimmed = true
    return result
  }
  return {
    ok: false,
    text: 'error usage: power auto on|off | power idle <ms> | power dim <0..100> | power wake <0..100>\n',
  }
}

function executeScreenCommand(screenCommand) {
  const parts = screenCommand.trim().split(/\s+/).filter(Boolean)
  const action = parts[0]?.toLowerCase()
  if (!action || action === 'status') {
    return { ok: true, text: `screen brightness ${state.screen.brightness}\n` }
  }
  if (action === 'brightness' || action === 'bright') {
    if (parts[1] === undefined) {
      return { ok: true, text: `screen brightness ${state.screen.brightness}\n` }
    }
    return setUserBrightness(parts[1])
  }
  if (action === 'dim' || action === 'cool' || action === 'sleep' || action === 'off') {
    const result = setScreenBrightness(parts[1] ?? state.power.dimBrightness)
    if (result.ok) state.power.dimmed = true
    return result
  }
  if (action === 'wake' || action === 'on') {
    const result = setScreenBrightness(parts[1] ?? state.power.wakeBrightness)
    if (result.ok) {
      state.power.dimmed = false
      noteActivity('screen:wake')
    }
    return result
  }
  return { ok: false, text: 'error usage: screen brightness <0..100> | screen sleep|wake\n' }
}

function executeLightCommand(robot, lightCommand) {
  const parts = lightCommand.trim().split(/\s+/).filter(Boolean)
  const action = parts[0]?.toLowerCase()
  if (!action) {
    return { ok: false, text: 'error usage: light on|blink|flash|rainbow|off ...\n' }
  }

  if (action === 'flash') {
    if (parts.length < 4) {
      return {
        ok: false,
        text: 'error usage: light flash <r> <g> <b> [duration-ms] [interval-ms]\n',
      }
    }
    if (state.mute.on) {
      return { ok: true, text: 'ok light flash skipped (muted)\n' }
    }
    const r = clamp(toNumber(parts[1]), 0, 255)
    const g = clamp(toNumber(parts[2]), 0, 255)
    const b = clamp(toNumber(parts[3]), 0, 255)
    const duration = Math.round(clamp(toNumber(parts[4], 1800), 200, 5000))
    const interval = Math.round(clamp(toNumber(parts[5], 110), 50, 1000))
    blinkHeadLight(robot, r, g, b, interval, duration)
    return { ok: true, text: `ok light flash head ${duration}\n` }
  }

  if (action === 'off') {
    const ledName = parts[1] ?? 'head'
    robot.lightOff(ledName)
    state.light.on = false
    return { ok: true, text: `ok light off ${ledName}\n` }
  }

  if (action === 'rainbow') {
    const ledName = parts[1] ?? 'head'
    robot.lightRainbow(ledName)
    state.light.on = true
    return { ok: true, text: `ok light rainbow ${ledName}\n` }
  }

  if (action === 'on' || action === 'blink') {
    if (parts.length < 4) {
      return { ok: false, text: `error usage: light ${action} <r> <g> <b> [ms] [led-name]\n` }
    }
    const r = clamp(toNumber(parts[1]), 0, 255)
    const g = clamp(toNumber(parts[2]), 0, 255)
    const b = clamp(toNumber(parts[3]), 0, 255)
    if (action === 'on') {
      const ledName = parts[4] ?? 'head'
      robot.lightOn(ledName, r, g, b)
      state.light.on = true
      return { ok: true, text: `ok light on ${ledName}\n` }
    }
    const duration = parts[4] !== undefined && Number.isFinite(Number(parts[4])) ? Number(parts[4]) : 250
    const ledName = parts[5] ?? 'head'
    robot.lightBlink(ledName, r, g, b, duration)
    return { ok: true, text: `ok light blink ${ledName}\n` }
  }

  return { ok: false, text: `error unknown light action: ${action}\n` }
}

function startCelebration(promise, label) {
  promise
    .then((result) => {
      if (!result.ok) trace(`[matchday] ${result.text}`)
    })
    .catch((error) => {
      state.lastError = `${label}: ${error}`
      trace(`[matchday] ${label} failed ${error}\n`)
    })
}

export async function runCommand(robot, line) {
  const command = String(line ?? '').trim()
  if (!command) {
    return { ok: true, text: '' }
  }

  state.lastCommand = command
  state.lastError = ''
  noteActivity('command')

  const lower = command.toLowerCase()

  try {
    if (lower === 'help') {
      return { ok: true, text: helpText() }
    }

    if (lower === 'status') {
      return { ok: true, text: JSON.stringify(statusPayload()) }
    }

    if (lower === 'diag' || lower === 'diagnostics' || lower === 'sensors' || lower === 'sensors status') {
      return { ok: true, text: JSON.stringify(diagnosticsPayload(robot)) }
    }

    if (lower === 'look away') {
      stopIdleLook(robot, true)
      return { ok: true, text: 'ok look away\n' }
    }

    if (lower.startsWith('idle ')) {
      return executeIdleCommand(robot, command.substring(5))
    }

    if (lower === 'power' || lower.startsWith('power ') || lower === 'cool' || lower.startsWith('cool ')) {
      const offset = lower.startsWith('cool') ? 4 : 5
      return executePowerCommand(robot, command.substring(offset))
    }

    if (lower === 'screen' || lower.startsWith('screen ')) {
      return executeScreenCommand(command.substring(6))
    }

    if (lower === 'mute' || lower.startsWith('mute ')) {
      return executeMuteCommand(robot, command.substring(4))
    }

    if (lower === 'tts' || lower.startsWith('tts ')) {
      return executeTtsCommand(command.substring(3))
    }

    if (lower === 'setup' || lower === 'setup status') {
      return { ok: true, text: JSON.stringify(state.setup) }
    }

    if (lower === 'setup hide' || lower === 'setup off') {
      return hideSetupQr(robot)
    }

    if (lower.startsWith('setup show ')) {
      return showSetupQr(robot, command.substring('setup show '.length))
    }

    if (lower.startsWith('brightness ')) {
      return setUserBrightness(command.substring(11).trim())
    }

    if (lower.startsWith('face ') || lower.startsWith('emotion ')) {
      const value = command.substring(command.indexOf(' ') + 1)
      const emotion = normalizeEmotion(value)
      if (!emotion) {
        return {
          ok: false,
          text: `error invalid emotion; available: ${EMOTIONS.join(', ')}\n`,
        }
      }
      robot.setEmotion(emotion)
      state.emotion = emotion
      return { ok: true, text: `ok emotion ${emotion}\n` }
    }

    if (lower.startsWith('look3 ')) {
      const values = parseNumbers(command.substring(6))
      if (values.length < 3) {
        return { ok: false, text: 'error usage: look3 <x> <y> <z>\n' }
      }
      const gaze = [values[0], values[1], values[2]]
      stopIdleLook(robot, false)
      robot.lookAt(gaze)
      state.gaze = gaze
      return { ok: true, text: `ok look3 ${gaze.join(' ')}\n` }
    }

    if (lower.startsWith('look ')) {
      const values = parseNumbers(command.substring(5))
      if (values.length < 2) {
        return { ok: false, text: 'error usage: look <screen-x> <screen-y>\n' }
      }
      const gaze = screenLookToVector(values[0], values[1])
      stopIdleLook(robot, false)
      robot.lookAt(gaze)
      state.gaze = gaze
      return { ok: true, text: `ok look ${values[0]} ${values[1]}\n` }
    }

    if (lower.startsWith('say ')) {
      const message = command.substring(4).trim()
      if (!message) {
        return { ok: false, text: 'error usage: say <text>\n' }
      }
      showBalloon(robot, message)
      const result = await speakRemote(message)
      scheduleBalloonAutoHide(robot, message, 4500)
      if (!result.ok) {
        state.lastError = result.text.trim()
        return {
          ok: true,
          text: `ok say (balloon shown; tts unavailable: ${result.text.trim()})\n`,
        }
      }
      return { ok: true, text: 'ok say\n' }
    }

    if (lower === 'voice') {
      return { ok: false, text: 'error usage: voice <fallback-clip-id> <text>\n' }
    }

    if (lower.startsWith('voice ')) {
      const value = command.substring('voice '.length).trim()
      const separator = value.indexOf(' ')
      if (separator < 1 || !value.substring(separator + 1).trim()) {
        return { ok: false, text: 'error usage: voice <fallback-clip-id> <text>\n' }
      }
      const fallbackClipId = value.substring(0, separator)
      const message = value.substring(separator + 1).trim()
      return playSpeechOrClip(robot, message, fallbackClipId)
    }

    if (lower === 'clip' || lower === 'clip list' || lower === 'clip status') {
      return { ok: true, text: `${FAN_CLIP_IDS.join('\n')}\n` }
    }

    if (lower.startsWith('clip ')) {
      return playFanClip(robot, command.substring(5))
    }

    if (lower.startsWith('celebrate say ')) {
      const parts = command.substring('celebrate say '.length).trim().split(/\s+/)
      const values = parts.slice(0, 3).map((value) => Number(value))
      const message = parts.slice(3).join(' ').trim()
      if (values.length < 3 || values.some((value) => !Number.isFinite(value)) || !message) {
        return { ok: false, text: 'error usage: celebrate say <r> <g> <b> <text>\n' }
      }
      if (state.celebrating) {
        return { ok: false, text: 'error celebration already in progress\n' }
      }
      const [red, green, blue] = values.map((value) => Math.round(clamp(value, 0, 255)))
      startCelebration(celebrateGoal(robot, red, green, blue, message), 'celebrate say')
      return { ok: true, text: `ok celebrate say started ${red} ${green} ${blue}\n` }
    }

    if (lower.startsWith('celebrate result ')) {
      const parts = command.substring('celebrate result '.length).trim().split(/\s+/)
      const outcome = parts[0]?.toLowerCase()
      const values = parts.slice(1, 4).map((value) => Number(value))
      const message = parts.slice(4).join(' ').trim()
      if (!['win', 'lose'].includes(outcome) || values.length < 3 || values.some((value) => !Number.isFinite(value))) {
        return { ok: false, text: 'error usage: celebrate result win|lose <r> <g> <b> [text]\n' }
      }
      if (state.celebrating) {
        return { ok: false, text: 'error celebration already in progress\n' }
      }
      const [red, green, blue] = values.map((value) => Math.round(clamp(value, 0, 255)))
      startCelebration(celebrateResult(robot, outcome, red, green, blue, message), 'celebrate result')
      return { ok: true, text: `ok celebrate result started ${outcome} ${red} ${green} ${blue}\n` }
    }

    if (lower === 'celebrate' || lower === 'celebrate goal' || lower === 'celebrate result' || lower === 'celebrate say') {
      return {
        ok: false,
        text: 'error usage: celebrate goal <r> <g> <b> | celebrate result win|lose <r> <g> <b> [text] | celebrate say <r> <g> <b> <text>\n',
      }
    }

    if (lower.startsWith('celebrate goal ')) {
      const values = parseNumbers(command.substring('celebrate goal '.length))
      if (values.length < 3) {
        return { ok: false, text: 'error usage: celebrate goal <r> <g> <b>\n' }
      }
      if (state.celebrating) {
        return { ok: false, text: 'error celebration already in progress\n' }
      }
      const [red, green, blue] = values.map((value) => Math.round(clamp(value, 0, 255)))
      startCelebration(celebrateGoal(robot, red, green, blue), 'celebrate goal')
      return { ok: true, text: `ok celebrate goal started ${red} ${green} ${blue}\n` }
    }

    if (lower === 'balloon off' || lower === 'balloon hide') {
      cancelBalloonAutoHide()
      hideBalloon(robot)
      return { ok: true, text: 'ok balloon off\n' }
    }

    if (lower.startsWith('balloon temp ')) {
      const rest = command.substring(13).trim()
      const separator = rest.indexOf(' ')
      if (separator < 1) {
        return { ok: false, text: 'error usage: balloon temp <milliseconds> <text>\n' }
      }
      const durationMs = Math.round(clamp(toNumber(rest.substring(0, separator), 8000), 1000, 30000))
      const message = rest.substring(separator + 1).trim()
      if (!message) {
        return { ok: false, text: 'error usage: balloon temp <milliseconds> <text>\n' }
      }
      temporaryBalloon(robot, message, durationMs)
      return { ok: true, text: `ok balloon temp ${durationMs}\n` }
    }

    if (lower.startsWith('balloon ')) {
      const message = command.substring(8).trim()
      if (!message) {
        return { ok: false, text: 'error usage: balloon <text>\n' }
      }
      cancelBalloonAutoHide()
      showBalloon(robot, message)
      return { ok: true, text: 'ok balloon\n' }
    }

    if (lower === 'ticker off' || lower === 'ticker hide') {
      return setTicker(robot, '')
    }

    if (lower.startsWith('ticker ')) {
      return setTicker(robot, command.substring(7))
    }

    if (lower === 'pkbar off' || lower === 'pkbar hide') {
      return hideProbabilityBar(robot)
    }

    if (lower === 'pkbar' || lower === 'pkbar status') {
      return { ok: true, text: JSON.stringify(state.probabilityBar) }
    }

    if (lower.startsWith('pkbar ')) {
      const parts = command.substring(6).trim().split(/\s+/)
      if (parts.length < 6) {
        return {
          ok: false,
          text: 'error usage: pkbar <left-flag> <left-%> <left-hex> <right-flag> <right-%> <right-hex> [icon]\n',
        }
      }
      const left = clamp(toNumber(parts[1], 50), 0, 100)
      const right = clamp(toNumber(parts[4], 50), 0, 100)
      const total = left + right
      const leftPercent = total > 0 ? Math.round((left * 100) / total) : 50
      return setProbabilityBar(robot, {
        leftFlag: parts[0],
        leftPercent,
        leftColor: parts[2],
        rightFlag: parts[3],
        rightColor: parts[5],
        icon: parts[6],
      })
    }

    if (lower.startsWith('pose ')) {
      const values = parseNumbers(command.substring(5))
      if (values.length < 2) {
        return { ok: false, text: 'error usage: pose <yaw-deg> <pitch-deg> [roll-deg] [seconds]\n' }
      }
      const yaw = values[0]
      const pitch = values[1]
      const roll = values.length >= 3 ? values[2] : 0
      const seconds = values.length >= 4 ? values[3] : 0.5
      stopIdleLook(robot, false)
      await robot.setTorque(true)
      state.torque = true
      await robot.setPose(
        { rotation: { y: toRadians(yaw), p: toRadians(pitch), r: toRadians(roll) } },
        seconds,
      )
      state.pose = { yaw, pitch, roll }
      return { ok: true, text: `ok pose ${yaw} ${pitch} ${roll}\n` }
    }

    if (lower.startsWith('servo ')) {
      const values = parseNumbers(command.substring(6))
      if (values.length < 2) {
        return { ok: false, text: 'error usage: servo <pan:0..180> <tilt:0..180>\n' }
      }
      const pan = clamp(values[0], 0, 180)
      const tilt = clamp(values[1], 0, 180)
      const yaw = pan - 90
      const pitch = tilt - 90
      stopIdleLook(robot, false)
      await robot.setTorque(true)
      state.torque = true
      await robot.setPose({ rotation: { y: toRadians(yaw), p: toRadians(pitch), r: 0 } }, 0.4)
      state.pose = { yaw, pitch, roll: 0 }
      return { ok: true, text: `ok servo ${pan} ${tilt}\n` }
    }

    if (lower.startsWith('torque ')) {
      const value = lower.substring(7).trim()
      const enabled = value === 'on' || value === 'true' || value === '1'
      const disabled = value === 'off' || value === 'false' || value === '0'
      if (!enabled && !disabled) {
        return { ok: false, text: 'error usage: torque on|off\n' }
      }
      await robot.setTorque(enabled)
      state.torque = enabled
      return { ok: true, text: `ok torque ${enabled ? 'on' : 'off'}\n` }
    }

    if (lower.startsWith('mouth ')) {
      const value = clamp(toNumber(command.substring(6), 0), 0, 1)
      robot.setMouthOpen(value)
      state.mouth = value
      return { ok: true, text: `ok mouth ${value}\n` }
    }

    if (lower.startsWith('color ')) {
      const parts = command.substring(6).trim().split(/\s+/)
      if (parts.length < 4 || !['primary', 'secondary'].includes(parts[0])) {
        return { ok: false, text: 'error usage: color primary|secondary <r> <g> <b>\n' }
      }
      const [key, r, g, b] = parts
      robot.setColor(key, clamp(toNumber(r), 0, 255), clamp(toNumber(g), 0, 255), clamp(toNumber(b), 0, 255))
      return { ok: true, text: `ok color ${key}\n` }
    }

    if (lower.startsWith('light ')) {
      return executeLightCommand(robot, command.substring(6))
    }

    if (lower.startsWith('tone ')) {
      const values = parseNumbers(command.substring(5))
      if (values.length < 2) {
        return { ok: false, text: 'error usage: tone <hz> <ms> [volume]\n' }
      }
      if (state.mute.on) {
        return { ok: true, text: 'ok tone skipped (muted)\n' }
      }
      await robot.tone(values[0], values[1], values.length >= 3 ? clamp(values[2], 0, 1) : undefined)
      return { ok: true, text: 'ok tone\n' }
    }
  } catch (error) {
    state.lastError = String(error)
    return { ok: false, text: `error ${error}\n` }
  }

  return {
    ok: false,
    text: `error unknown command: ${command}\n${helpText()}`,
  }
}

export async function executeJsonAction(robot, payload) {
  const action = String(payload?.action ?? '').toLowerCase()
  if (!action) {
    return runCommand(robot, payload?.command ?? '')
  }

  if (action === 'status') {
    return { ok: true, text: JSON.stringify(statusPayload()) }
  }
  if (action === 'diag' || action === 'diagnostics' || action === 'sensors') {
    return { ok: true, text: JSON.stringify(diagnosticsPayload(robot)) }
  }
  if (action === 'say') {
    return runCommand(robot, `say ${payload.text ?? payload.message ?? ''}`)
  }
  if (action === 'clip' || action === 'fan_clip') {
    return runCommand(robot, `clip ${payload.id ?? payload.clip ?? payload.name ?? ''}`)
  }
  if (action === 'celebrate' || action === 'goal_celebration') {
    return runCommand(robot, `celebrate goal ${payload.r ?? 255} ${payload.g ?? 255} ${payload.b ?? 255}`)
  }
  if (action === 'face' || action === 'emotion') {
    return runCommand(robot, `emotion ${payload.emotion ?? payload.face ?? ''}`)
  }
  if (action === 'look') {
    if (payload.x !== undefined && payload.y !== undefined && payload.z !== undefined) {
      return runCommand(robot, `look3 ${payload.x} ${payload.y} ${payload.z}`)
    }
    return runCommand(robot, `look ${payload.screenX ?? payload.x ?? 0} ${payload.screenY ?? payload.y ?? 0}`)
  }
  if (action === 'idle' || action === 'idle_look') {
    return runCommand(robot, `idle look ${payload.enabled ? 'on' : 'off'} ${payload.intervalMs ?? ''}`)
  }
  if (action === 'balloon') {
    if (payload.visible === false) return runCommand(robot, 'balloon off')
    const durationMs = payload.durationMs ?? payload.timeoutMs
    return runCommand(
      robot,
      durationMs
        ? `balloon temp ${durationMs} ${payload.text ?? payload.message ?? ''}`
        : `balloon ${payload.text ?? payload.message ?? ''}`,
    )
  }
  if (action === 'ticker' || action === 'market_ticker') {
    return runCommand(robot, payload.visible === false ? 'ticker off' : `ticker ${payload.text ?? payload.message ?? ''}`)
  }
  if (action === 'pkbar' || action === 'probability_bar') {
    if (payload.visible === false) return runCommand(robot, 'pkbar off')
    const icon = payload.icon ? ` ${payload.icon}` : ''
    return runCommand(
      robot,
      `pkbar ${payload.leftFlag ?? 'fr'} ${payload.leftPercent ?? 50} ${payload.leftColor ?? '2457a6'} ${payload.rightFlag ?? 'ma'} ${payload.rightPercent ?? 50} ${payload.rightColor ?? 'c1272d'}${icon}`,
    )
  }
  if (action === 'pose') {
    return runCommand(robot, `pose ${payload.yaw ?? 0} ${payload.pitch ?? 0} ${payload.roll ?? 0} ${payload.seconds ?? 0.5}`)
  }
  if (action === 'torque') {
    return runCommand(robot, `torque ${payload.enabled ? 'on' : 'off'}`)
  }
  if (action === 'mouth') {
    return runCommand(robot, `mouth ${payload.value ?? 0}`)
  }
  if (action === 'light') {
    const mode = payload.mode ?? payload.effect ?? 'on'
    if (mode === 'off' || mode === 'rainbow') {
      return runCommand(robot, `light ${mode} ${payload.led ?? payload.name ?? ''}`)
    }
    if (mode === 'blink') {
      return runCommand(
        robot,
        `light blink ${payload.r ?? 0} ${payload.g ?? 0} ${payload.b ?? 0} ${payload.ms ?? 250} ${payload.led ?? payload.name ?? ''}`,
      )
    }
    if (mode === 'flash') {
      return runCommand(
        robot,
        `light flash ${payload.r ?? 0} ${payload.g ?? 0} ${payload.b ?? 0} ${payload.durationMs ?? payload.ms ?? 1800} ${payload.intervalMs ?? 110}`,
      )
    }
    return runCommand(robot, `light on ${payload.r ?? 0} ${payload.g ?? 0} ${payload.b ?? 0} ${payload.led ?? payload.name ?? ''}`)
  }
  if (action === 'screen' || action === 'brightness' || action === 'backlight') {
    const mode = String(payload.mode ?? payload.command ?? '').toLowerCase()
    if (mode === 'sleep' || mode === 'off' || mode === 'wake' || mode === 'on' || mode === 'dim' || mode === 'cool') {
      return runCommand(robot, `screen ${mode} ${payload.brightness ?? payload.value ?? payload.level ?? ''}`)
    }
    return runCommand(robot, `screen brightness ${payload.brightness ?? payload.value ?? payload.level ?? ''}`)
  }
  if (action === 'power' || action === 'cool') {
    if (payload.command) return runCommand(robot, `power ${payload.command}`)
    if (payload.autoDim !== undefined) return runCommand(robot, `power auto ${payload.autoDim ? 'on' : 'off'}`)
    if (payload.idleMs !== undefined) return runCommand(robot, `power idle ${payload.idleMs}`)
    if (payload.dimBrightness !== undefined) return runCommand(robot, `power dim ${payload.dimBrightness}`)
    if (payload.wakeBrightness !== undefined) return runCommand(robot, `power wake ${payload.wakeBrightness}`)
    return runCommand(robot, 'power status')
  }
  if (action === 'tts') {
    if (payload.host !== undefined) return runCommand(robot, `tts host ${payload.host || 'clear'}`)
    return runCommand(robot, 'tts status')
  }
  if (action === 'mute' || action === 'boss_key') {
    if (payload.enabled !== undefined) {
      return runCommand(robot, payload.enabled ? `mute on ${payload.minutes ?? ''}` : 'mute off')
    }
    return runCommand(robot, 'mute status')
  }
  if (action === 'tone') {
    return runCommand(robot, `tone ${payload.hz ?? payload.frequency ?? 440} ${payload.ms ?? payload.duration ?? 200} ${payload.volume ?? ''}`)
  }
  if (action === 'color') {
    return runCommand(robot, `color ${payload.key ?? payload.name ?? 'primary'} ${payload.r ?? 0} ${payload.g ?? 0} ${payload.b ?? 0}`)
  }
  if (action === 'setup') {
    if (payload.url) return runCommand(robot, `setup show ${payload.url}`)
    if (payload.visible === false) return runCommand(robot, 'setup hide')
    return runCommand(robot, 'setup status')
  }

  return { ok: false, text: `error unknown json action: ${action}\n` }
}
