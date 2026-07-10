// Shared constants, persisted preferences, runtime state, and small utilities.
import Modules from 'modules'
import Net from 'net'
import Time from 'time'

export const MOD_NAME = 'stackchan_matchday'
export const MOD_VERSION = '1.3.0'
export const PREF_DOMAIN = 'stackchan'

export const HTTP_PORT = 80
export const AP_SSID = 'StackChan-Matchday'
export const AP_PASSWORD = 'stackchan'

export const DEFAULT_IDLE_LOOK_INTERVAL_MS = 5000
export const MIN_IDLE_LOOK_INTERVAL_MS = 1000
export const MAX_IDLE_LOOK_INTERVAL_MS = 60000
export const DEFAULT_WAKE_BRIGHTNESS = 45
export const DEFAULT_DIM_BRIGHTNESS = 12
export const DEFAULT_POWER_IDLE_MS = 30000
export const POWER_CHECK_INTERVAL_MS = 5000

export const TICKER_HEIGHT = 36
export const PK_BAR_TOP = 18
export const PK_BAR_HEIGHT = 36
export const FLAG_WIDTH = 24
export const FLAG_HEIGHT = 20

export const EMOTIONS = ['NEUTRAL', 'HAPPY', 'SLEEPY', 'DOUBTFUL', 'SAD', 'ANGRY', 'COLD', 'HOT']
const EMOTION_ALIASES = new Map([
  ['neutral', 'NEUTRAL'],
  ['normal', 'NEUTRAL'],
  ['happy', 'HAPPY'],
  ['smile', 'HAPPY'],
  ['sleep', 'SLEEPY'],
  ['sleepy', 'SLEEPY'],
  ['surprise', 'DOUBTFUL'],
  ['surprised', 'DOUBTFUL'],
  ['doubtful', 'DOUBTFUL'],
  ['sad', 'SAD'],
  ['angry', 'ANGRY'],
  ['cold', 'COLD'],
  ['hot', 'HOT'],
])

export let Preference
try {
  Preference = Modules.importNow('preference')
} catch (_error) {
  trace('[matchday] preference module unavailable; settings will not persist\n')
}

export const state = {
  emotion: 'NEUTRAL',
  speech: '',
  balloon: '',
  ticker: '',
  probabilityBar: {
    visible: false,
    position: 'top',
    leftFlag: '',
    leftPercent: 50,
    leftColor: '#2457a6',
    rightFlag: '',
    rightPercent: 50,
    rightColor: '#c1272d',
  },
  setup: {
    visible: false,
    url: '',
    trigger: {
      available: false,
      sources: [],
      lastSource: '',
      lastTriggeredTicks: 0,
      // Live view of the raw Si12T top-bar detector, for threshold tuning
      // over /api/status without reflashing.
      touch: {
        intensity: 0,
        position: 0,
        active: false,
        taps: 0,
        lastTapMs: 0,
        lastDrift: 0,
      },
    },
  },
  matchSetup: {
    language: 'zh',
    options: [],
    current: {},
    pending: null,
    lastResult: null,
  },
  gaze: null,
  pose: { yaw: 0, pitch: 0, roll: 0 },
  torque: false,
  mouth: 0,
  idle: {
    look: false,
    intervalMs: DEFAULT_IDLE_LOOK_INTERVAL_MS,
  },
  light: {
    on: false,
  },
  screen: {
    brightness: DEFAULT_WAKE_BRIGHTNESS,
  },
  power: {
    autoDim: true,
    idleMs: DEFAULT_POWER_IDLE_MS,
    dimBrightness: DEFAULT_DIM_BRIGHTNESS,
    wakeBrightness: DEFAULT_WAKE_BRIGHTNESS,
    dimmed: false,
    lastActivityTicks: 0,
    lastActivity: '',
  },
  tts: {
    busy: false,
  },
  diagnostics: {
    startedTicks: 0,
  },
  celebrating: false,
  lastCommand: '',
  lastError: '',
  accessPoint: null,
}

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

export function toNumber(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

export function readPreference(key, fallback, domain = PREF_DOMAIN) {
  if (!Preference) return fallback
  try {
    const value = Preference.get(domain, key)
    return value === undefined ? fallback : value
  } catch (_error) {
    return fallback
  }
}

export function savePreference(key, value, domain = PREF_DOMAIN) {
  if (!Preference) return false
  try {
    if (value === undefined || value === null) {
      Preference.delete(domain, key)
    } else if (readPreference(key, undefined, domain) !== value) {
      Preference.set(domain, key, value)
    }
    return true
  } catch (error) {
    trace(`[matchday] preference save ${key} failed ${error}\n`)
    return false
  }
}

export function nowTicks() {
  return Time.ticks
}

export function elapsedSince(ticks) {
  if (!ticks) return 0
  return Math.max(0, nowTicks() - ticks)
}

export function toRadians(degrees) {
  return (degrees * Math.PI) / 180
}

export function parseNumbers(text) {
  return text
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value))
}

export function normalizeEmotion(value) {
  const key = String(value ?? '').trim()
  if (!key) {
    return undefined
  }
  const upper = key.toUpperCase()
  if (EMOTIONS.includes(upper)) {
    return upper
  }
  return EMOTION_ALIASES.get(key.toLowerCase())
}

export function normalizeHexColor(value, fallback) {
  const raw = String(value ?? '')
    .trim()
    .replace(/^#/, '')
  if (!/^[0-9a-fA-F]{6}$/.test(raw)) return fallback
  return `#${raw.toLowerCase()}`
}

export function contrastColor(hex) {
  const value = Number.parseInt(String(hex).replace('#', ''), 16)
  const r = (value >> 16) & 0xff
  const g = (value >> 8) & 0xff
  const b = value & 0xff
  return r * 299 + g * 587 + b * 114 > 150000 ? '#101010' : '#ffffff'
}

export function safeNetGet(key) {
  try {
    return Net.get(key) ?? ''
  } catch (_error) {
    return ''
  }
}

export function hasUsableIp(ip) {
  const value = String(ip ?? '').trim()
  return value !== '' && value !== '0.0.0.0' && value !== '::'
}

export function networkPayload() {
  return {
    ssid: safeNetGet('SSID'),
    ip: safeNetGet('IP'),
    accessPoint: state.accessPoint,
  }
}

export function screenLookToVector(x, y) {
  return [0.6, clamp(x, -12, 12) / 30, -clamp(y, -8, 8) / 40]
}
