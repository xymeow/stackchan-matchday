// Sound and motion feedback: tone-based fan clips, remote-TTS speech with
// tone fallback, and goal / final-result celebrations.
//
// The slim mod ships no audio assets. Dynamic speech streams from the LAN TTS
// server (tools/stackchan_tts_server.py) through the host's `tts-remote`
// module; every named reaction also has a short tone fallback.
import Modules from 'modules'
import { asyncWait } from 'stackchan-util'
import Timer from 'timer'
import {
  clamp,
  readPreference,
  savePreference,
  state,
  toRadians,
} from 'matchday/state'
import { noteActivity, startIdleLook, stopIdleLook } from 'matchday/ui'

const TONE_VOLUME = 0.8
const TTS_DOMAIN = 'tts'
const TTS_DEFAULT_PORT = 8787
const TTS_SAMPLE_RATE = 24000
const TTS_VOLUME = 0.7

// Tone patterns per named reaction: [hz, ms] pairs played in order.
const TONE_CLIPS = new Map([
  ['ready', [[880, 120]]],
  ['match-start', [[523, 150], [659, 150], [784, 220]]],
  ['favorite-goal', [[523, 120], [659, 120], [784, 120], [1047, 320]]],
  ['opponent-goal', [[440, 180], [349, 180], [262, 300]]],
  ['favorite-penalty-scored', [[659, 120], [784, 120], [1047, 260]]],
  ['favorite-penalty-missed', [[392, 200], [330, 300]]],
  ['opponent-penalty-scored', [[349, 200], [262, 300]]],
  ['opponent-penalty-missed', [[659, 140], [784, 240]]],
  ['penalty-awarded', [[659, 120], [659, 120], [880, 240]]],
  ['favorite-red-card', [[330, 250], [294, 350]]],
  ['opponent-red-card', [[587, 140], [659, 240]]],
  ['yellow-card', [[494, 200]]],
  ['corner', [[587, 110], [659, 110]]],
  ['foul', [[440, 140]]],
  ['odds-up', [[587, 110], [740, 160]]],
  ['odds-down', [[740, 110], [587, 160]]],
  ['favorite-win', [[523, 140], [659, 140], [784, 140], [1047, 200], [784, 140], [1047, 400]]],
  ['favorite-lose', [[392, 250], [349, 250], [330, 250], [262, 450]]],
])

export const FAN_CLIP_IDS = [...TONE_CLIPS.keys()]

export async function playFanClip(robot, clipId) {
  const id = String(clipId ?? '')
    .trim()
    .toLowerCase()
  const pattern = TONE_CLIPS.get(id)
  if (!pattern) {
    return {
      ok: false,
      text: `error invalid clip; available: ${FAN_CLIP_IDS.join(', ')}\n`,
    }
  }
  if (state.mute.on) {
    return { ok: true, text: `ok clip ${id} skipped (muted)\n` }
  }
  // A tone during an active TTS stream fights over the speaker and errors;
  // callers treat that as delivery failure and retry, so skip successfully.
  if (state.tts.busy) {
    return { ok: true, text: `ok clip ${id} skipped (tts busy)\n` }
  }
  try {
    for (const [hz, ms] of pattern) {
      await robot.tone(hz, ms, TONE_VOLUME)
    }
    return { ok: true, text: `ok clip ${id}\n` }
  } catch (error) {
    state.lastError = `clip ${id}: ${error}`
    return { ok: false, text: `error clip ${id}: ${error}\n` }
  }
}

// ---------------------------------------------------------------------------
// Remote TTS. Host/port live in the host firmware's native `tts` preference
// domain, so `tts host <ip[:port]>` takes effect immediately — the mod owns
// its own tts-remote instance and recreates it when the preference changes.

let remoteTts
let remoteTtsKey = ''

export function ttsTarget() {
  const host = String(readPreference('host', '', TTS_DOMAIN) ?? '')
  const port = Number(readPreference('port', TTS_DEFAULT_PORT, TTS_DOMAIN))
  return {
    host,
    port: Number.isInteger(port) && port > 0 && port <= 65535 ? port : TTS_DEFAULT_PORT,
  }
}

export function ttsPayload() {
  const target = ttsTarget()
  return {
    host: target.host || null,
    port: target.port,
    busy: state.tts.busy,
  }
}

function ensureRemoteTts() {
  const target = ttsTarget()
  if (!target.host) return undefined
  const key = `${target.host}:${target.port}`
  if (remoteTts === undefined || remoteTtsKey !== key) {
    const { TTS } = Modules.importNow('tts-remote')
    remoteTts = new TTS({
      host: target.host,
      port: target.port,
      sampleRate: TTS_SAMPLE_RATE,
      volume: TTS_VOLUME,
    })
    remoteTtsKey = key
  }
  return remoteTts
}

export async function speakRemote(text) {
  const speech = String(text ?? '').trim()
  if (!speech) return { ok: false, text: 'error empty speech\n' }
  if (state.mute.on) {
    return { ok: true, text: 'ok speech skipped (muted)\n' }
  }
  const tts = ensureRemoteTts()
  if (!tts) {
    return { ok: false, text: 'error tts host unset; run: tts host <ip[:port]>\n' }
  }
  if (state.tts.busy) {
    return { ok: false, text: 'error tts busy\n' }
  }
  state.tts.busy = true
  try {
    state.speech = speech
    await tts.stream(`/say?text=${encodeURIComponent(speech)}`)
    return { ok: true, text: 'ok voice remote\n' }
  } catch (error) {
    state.lastError = `tts: ${error}`
    return { ok: false, text: `error tts: ${error}\n` }
  } finally {
    state.tts.busy = false
  }
}

export async function playSpeechOrClip(robot, message, fallbackClipId) {
  const speech = String(message ?? '').trim()
  if (!speech) return playFanClip(robot, fallbackClipId)
  const result = await speakRemote(speech)
  if (result.ok) return result
  trace(`[matchday] remote tts unavailable; falling back to clip ${fallbackClipId}\n`)
  return playFanClip(robot, fallbackClipId)
}

export function executeTtsCommand(ttsCommand) {
  const parts = ttsCommand.trim().split(/\s+/).filter(Boolean)
  const action = parts[0]?.toLowerCase()
  if (!action || action === 'status') {
    const tts = ttsPayload()
    return {
      ok: true,
      text: `tts host ${tts.host ?? '(unset)'} port ${tts.port}\n`,
    }
  }
  if (action === 'host') {
    const value = parts[1]
    if (!value) {
      return executeTtsCommand('status')
    }
    const lowerValue = value.toLowerCase()
    if (lowerValue === 'clear' || lowerValue === 'off' || lowerValue === 'default' || lowerValue === 'reset') {
      savePreference('host', undefined, TTS_DOMAIN)
      savePreference('port', undefined, TTS_DOMAIN)
      savePreference('ttsHost', undefined)
      remoteTts = undefined
      remoteTtsKey = ''
      return { ok: true, text: 'ok tts host cleared\n' }
    }
    if (!/^[0-9A-Za-z_.-]+(:\d{1,5})?$/.test(value)) {
      return {
        ok: false,
        text: 'error usage: tts host <ip[:port]> | tts host clear\n',
      }
    }
    const separator = value.lastIndexOf(':')
    const host = separator > 0 ? value.slice(0, separator) : value
    const port = separator > 0 ? Number(value.slice(separator + 1)) : TTS_DEFAULT_PORT
    if (!savePreference('host', host, TTS_DOMAIN)) {
      return { ok: false, text: 'error tts persistence unavailable\n' }
    }
    savePreference('port', port, TTS_DOMAIN)
    // Mirror the older preference key for hosts that still read stackchan/ttsHost.
    savePreference('ttsHost', value)
    remoteTts = undefined
    remoteTtsKey = ''
    return { ok: true, text: `ok tts host ${host}:${port}\n` }
  }
  return {
    ok: false,
    text: 'error usage: tts status | tts host <ip[:port]> | tts host clear\n',
  }
}

// ---------------------------------------------------------------------------
// Celebrations: safe-amplitude head dance + head light + voice. Remote speech
// starts with the first pose so the reaction feels like one coordinated event.

let topLightTimer

export function blinkHeadLight(robot, r, g, b, ms = 220, restoreMs = 900) {
  robot.lightBlink('head', r, g, b, ms)
  state.light.on = true
  if (topLightTimer !== undefined) {
    Timer.clear(topLightTimer)
  }
  topLightTimer = Timer.set(() => {
    topLightTimer = undefined
    robot.lightOff('head')
    state.light.on = false
  }, restoreMs)
}

const GOAL_POSES = [
  { yaw: -16, pitch: -5, seconds: 0.2 },
  { yaw: 16, pitch: 2, seconds: 0.2 },
  { yaw: -13, pitch: -8, seconds: 0.2 },
  { yaw: 13, pitch: 1, seconds: 0.2 },
  { yaw: -16, pitch: -4, seconds: 0.2 },
  { yaw: 16, pitch: -7, seconds: 0.2 },
  { yaw: -11, pitch: 2, seconds: 0.18 },
  { yaw: 11, pitch: -5, seconds: 0.18 },
  { yaw: 0, pitch: 0, seconds: 0.24 },
]

const WIN_POSES = [
  { yaw: -18, pitch: -7, seconds: 0.22 },
  { yaw: 18, pitch: -7, seconds: 0.22 },
  { yaw: -14, pitch: 3, seconds: 0.2 },
  { yaw: 14, pitch: -8, seconds: 0.2 },
  { yaw: -10, pitch: 1, seconds: 0.18 },
  { yaw: 10, pitch: -6, seconds: 0.18 },
  { yaw: 0, pitch: 0, seconds: 0.25 },
]

const LOSE_POSES = [
  // CoreS3's SCServo driver clamps pitch at +10 degrees. Keep the whole
  // dejected nod inside that range so each step remains distinct.
  { yaw: -8, pitch: 6, seconds: 0.35 },
  { yaw: 8, pitch: 8, seconds: 0.35 },
  { yaw: -7, pitch: 10, seconds: 0.35 },
  { yaw: 7, pitch: 9, seconds: 0.35 },
  { yaw: 0, pitch: 8, seconds: 0.4 },
  { yaw: 0, pitch: 0, seconds: 0.3 },
]

async function runCelebration(robot, { label, poses, emotion, mouth, light, blinkMs, blinkRestoreMs, voice }) {
  if (state.mute.on) {
    // Boss key: no sound, no dancing, no lights. The watcher's balloon has
    // already announced the event silently.
    return { ok: true, text: `ok ${label} skipped (muted)\n` }
  }
  if (state.celebrating) {
    return { ok: false, text: 'error celebration already in progress\n' }
  }
  state.celebrating = true
  const [r, g, b] = light
  const previousTorque = state.torque
  const previousPose = { ...state.pose }
  const resumeIdleLook = state.idle.look
  const idleIntervalMs = state.idle.intervalMs
  let voiceOutcomePromise
  let operationError
  let restoreError

  try {
    try {
      stopIdleLook(robot, false)
      robot.setEmotion(emotion)
      state.emotion = emotion
      robot.setMouthOpen(mouth)
      state.mouth = mouth
      blinkHeadLight(robot, r, g, b, blinkMs, blinkRestoreMs)
      await robot.setTorque(true)
      state.torque = true
      // Attach both resolve and reject handlers before starting motion. A
      // remote TTS constructor/stream failure can therefore never become an
      // unhandled rejection while the pose loop is running.
      voiceOutcomePromise = Promise.resolve()
        .then(() => voice())
        .then(
          (result) => ({ result }),
          (error) => ({ error }),
        )

      for (const pose of poses) {
        await robot.setPose(
          {
            rotation: {
              y: toRadians(pose.yaw),
              p: toRadians(pose.pitch),
              r: 0,
            },
          },
          pose.seconds,
        )
        state.pose = { yaw: pose.yaw, pitch: pose.pitch, roll: 0 }
        // setPose writes the servo goal time but resolves before that time has
        // elapsed. Keep every target visible before sending the next one.
        await asyncWait(Math.round(pose.seconds * 1000))
      }
    } catch (error) {
      operationError = error
    } finally {
      // Restore the physical mechanism before waiting for LAN audio. The
      // tts-remote stream has no hard timeout, so speech must never hold the
      // servos energized or leave the head between poses.
      try {
        await robot.setPose(
          {
            rotation: {
              y: toRadians(previousPose.yaw),
              p: toRadians(previousPose.pitch),
              r: toRadians(previousPose.roll),
            },
          },
          0.25,
        )
        state.pose = previousPose
        await asyncWait(250)
      } catch (error) {
        restoreError = `pose: ${error}`
      }
      if (!previousTorque) {
        try {
          await robot.setTorque(false)
          state.torque = false
        } catch (error) {
          restoreError = restoreError
            ? `${restoreError}; torque: ${error}`
            : `torque: ${error}`
        }
      }
    }

    if (operationError || restoreError) {
      const error = [operationError, restoreError && `restore ${restoreError}`]
        .filter(Boolean)
        .join('; ')
      state.lastError = `${label}: ${error}`
      return { ok: false, text: `error ${label}: ${error}\n` }
    }

    const voiceOutcome = await voiceOutcomePromise
    if (voiceOutcome.error) {
      state.lastError = `${label} voice: ${voiceOutcome.error}`
      return { ok: false, text: `error ${label} voice: ${voiceOutcome.error}\n` }
    }
    if (!voiceOutcome.result.ok) return voiceOutcome.result
    return { ok: true, text: `ok ${label} ${r} ${g} ${b}\n` }
  } finally {
    robot.setMouthOpen(0)
    state.mouth = 0
    state.celebrating = false
    if (resumeIdleLook) startIdleLook(robot, idleIntervalMs)
  }
}

export function celebrateGoal(robot, r, g, b, speech = '') {
  noteActivity('celebrate')
  return runCelebration(robot, {
    label: 'celebrate goal',
    poses: GOAL_POSES,
    emotion: 'HAPPY',
    mouth: 0.72,
    light: [clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255)],
    blinkMs: 90,
    blinkRestoreMs: 4800,
    voice: () => (speech ? playSpeechOrClip(robot, speech, 'favorite-goal') : playFanClip(robot, 'favorite-goal')),
  })
}

export function celebrateResult(robot, outcome, r, g, b, speech = '') {
  const won = outcome === 'win'
  noteActivity('celebrate')
  return runCelebration(robot, {
    label: `celebrate result ${outcome}`,
    poses: won ? WIN_POSES : LOSE_POSES,
    emotion: won ? 'HAPPY' : 'SAD',
    mouth: won ? 0.62 : 0.18,
    light: [clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255)],
    blinkMs: won ? 100 : 280,
    blinkRestoreMs: won ? 4200 : 3600,
    voice: () => (
      speech
        ? playSpeechOrClip(robot, speech, won ? 'favorite-win' : 'favorite-lose')
        : playFanClip(robot, won ? 'favorite-win' : 'favorite-lose')
    ),
  })
}
