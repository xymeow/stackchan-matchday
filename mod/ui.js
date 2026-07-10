// On-screen effects drawn by the mod itself (renderer decorators), plus
// screen brightness, auto-dim, and idle-look control.
//
// The speech balloon here replaces robot.showBalloon entirely so the mod
// controls its own font, size, and marquee behavior without host patches.
import { Container, Content, Label, Skin, Style, Texture } from 'piu/MC'
import Resource from 'Resource'
import { randomBetween } from 'stackchan-util'
import Time from 'time'
import Timer from 'timer'
import {
  FLAG_HEIGHT,
  FLAG_WIDTH,
  MAX_IDLE_LOOK_INTERVAL_MS,
  MIN_IDLE_LOOK_INTERVAL_MS,
  PK_BAR_HEIGHT,
  PK_BAR_TOP,
  POWER_CHECK_INTERVAL_MS,
  TICKER_HEIGHT,
  clamp,
  contrastColor,
  elapsedSince,
  normalizeHexColor,
  savePreference,
  state,
  toNumber,
} from 'matchday/state'

// Prefer the CJK font when the host firmware bundles it (see host/cjk-font/),
// otherwise fall back to the stock Stack-chan font. Percent digits render fine
// either way; CJK balloon text needs the host font commit.
export const FONT = (() => {
  for (const candidate of ['StackChanCN-24.fnt', 'StackChanCN-24.bf4']) {
    try {
      new Resource(candidate)
      return 'StackChanCN-24'
    } catch (_error) {
      // keep probing
    }
  }
  trace('[matchday] StackChanCN-24 not found in host; using OpenSans-Regular-24\n')
  return 'OpenSans-Regular-24'
})()

// ---------------------------------------------------------------------------
// Speech balloon with horizontal marquee for long lines.

const BALLOON = {
  left: 16,
  right: 16,
  bottom: 12,
  height: 58,
  paddingX: 20,
  paddingY: 12,
  holdMs: 700,
  pixelsPerSecond: 42,
  maxLoops: 3,
}

let balloonEffect
let balloonLabel
let balloonStyle
let balloonLineHeight
let balloonRestoreTimer

function ensureBalloonStyle() {
  if (balloonStyle === undefined) {
    balloonStyle = new Style({ font: FONT, color: '#000000', horizontal: 'left' })
    balloonLineHeight = Math.max(1, balloonStyle.measure('Mg').height ?? 24)
  }
  return balloonStyle
}

function balloonBackgroundSkin() {
  try {
    return new Skin({
      texture: new Texture('bubble.png'),
      color: ['#ffffff'],
      x: 0,
      y: 0,
      width: 204,
      height: 332,
      left: 24,
      right: 24,
      top: 12,
      bottom: 12,
    })
  } catch (_error) {
    return new Skin({ fill: '#ffffff' })
  }
}

class BalloonBehavior extends Behavior {
  onCreate(container, data) {
    this.textWidth = 1
    this.scrollDelta = 0
    this.scrollFrom = 0
    this.scrollTo = 1
    this.loops = 0
    this.text = data?.text ?? ''
  }

  textTop() {
    return Math.max(BALLOON.paddingY, Math.round((BALLOON.height - balloonLineHeight) / 2))
  }

  placeLabel(offset) {
    if (!balloonLabel) return
    balloonLabel.x = balloonEffect.x + BALLOON.paddingX - offset
    balloonLabel.y = balloonEffect.y + this.textTop()
  }

  onDisplaying(container) {
    const style = ensureBalloonStyle()
    const available = Math.max(1, container.width - BALLOON.paddingX * 2)
    this.textWidth = Math.max(available, Math.ceil(style.measure(this.text || ' ').width ?? 0) + 8)
    balloonLabel.coordinates = {
      left: BALLOON.paddingX,
      top: this.textTop(),
      width: this.textWidth,
      height: Math.max(balloonLineHeight, BALLOON.height - BALLOON.paddingY * 2),
    }
    const delta = Math.max(0, this.textWidth - available)
    this.scrollDelta = delta
    this.loops = 0
    if (delta <= 0) {
      this.placeLabel(0)
      return
    }
    const scrollMs = Math.max(900, Math.round((1000 * delta) / BALLOON.pixelsPerSecond))
    const duration = BALLOON.holdMs * 2 + scrollMs
    this.scrollFrom = BALLOON.holdMs / duration
    this.scrollTo = (BALLOON.holdMs + scrollMs) / duration
    container.interval = 33
    container.duration = duration
    container.time = 0
    this.placeLabel(0)
    container.start()
  }

  onTimeChanged(container) {
    if (this.scrollDelta <= 0) return
    const fraction = container.fraction
    let offset = 0
    if (fraction <= this.scrollFrom) {
      offset = 0
    } else if (fraction >= this.scrollTo) {
      offset = this.scrollDelta
    } else {
      offset = Math.round((this.scrollDelta * (fraction - this.scrollFrom)) / (this.scrollTo - this.scrollFrom))
    }
    this.placeLabel(offset)
  }

  onFinished(container) {
    if (this.scrollDelta <= 0) return
    this.loops += 1
    if (this.loops >= BALLOON.maxLoops) {
      container.stop()
      hideBalloon(currentBalloonRobot)
      return
    }
    container.time = 0
    this.placeLabel(0)
    container.start()
  }
}

let currentBalloonRobot

function createBalloonEffect(text) {
  ensureBalloonStyle()
  balloonLabel = new Label(null, {
    left: BALLOON.paddingX,
    top: BALLOON.paddingY,
    width: 1,
    height: Math.max(balloonLineHeight, BALLOON.height - BALLOON.paddingY * 2),
    string: text,
    style: balloonStyle,
  })
  return new Container(
    { text },
    {
      name: 'MatchdayBalloon',
      left: BALLOON.left,
      right: BALLOON.right,
      bottom: BALLOON.bottom,
      height: BALLOON.height,
      clip: true,
      active: false,
      contents: [
        new Content(null, {
          left: 0,
          right: 0,
          top: 0,
          bottom: 0,
          skin: balloonBackgroundSkin(),
        }),
        balloonLabel,
      ],
      Behavior: BalloonBehavior,
    },
  )
}

export function showBalloon(robot, message) {
  const text = String(message ?? '').replace(/\s*\n\s*/g, ' ')
  hideBalloon(robot)
  if (!text.trim()) return
  currentBalloonRobot = robot
  balloonEffect = createBalloonEffect(text)
  robot.renderer.addDecorator(balloonEffect)
  keepSetupQrOnTop(robot)
  state.balloon = text
}

export function hideBalloon(robot) {
  if (balloonEffect !== undefined && robot) {
    try {
      balloonEffect.stop()
    } catch (_error) {
      // container may not be animating
    }
    robot.renderer.removeDecorator(balloonEffect)
  }
  balloonEffect = undefined
  balloonLabel = undefined
  state.balloon = ''
}

export function cancelBalloonAutoHide() {
  if (balloonRestoreTimer !== undefined) {
    Timer.clear(balloonRestoreTimer)
    balloonRestoreTimer = undefined
  }
}

export function scheduleBalloonAutoHide(robot, message, ms) {
  cancelBalloonAutoHide()
  balloonRestoreTimer = Timer.set(() => {
    balloonRestoreTimer = undefined
    if (state.balloon === message.replace(/\s*\n\s*/g, ' ')) {
      hideBalloon(robot)
    }
  }, ms)
}

export function temporaryBalloon(robot, message, ms = 1800) {
  showBalloon(robot, message)
  scheduleBalloonAutoHide(robot, message, ms)
}

// ---------------------------------------------------------------------------
// Legacy one-line market ticker.

let tickerEffect
let tickerLabel

function createTickerEffect(message) {
  tickerLabel = new Label(null, {
    left: 6,
    right: 6,
    top: 0,
    bottom: 0,
    string: message,
    style: new Style({
      font: FONT,
      color: '#ffffff',
      horizontal: 'center',
      vertical: 'middle',
    }),
  })
  return new Container(null, {
    name: 'MarketTicker',
    left: 0,
    right: 0,
    bottom: 0,
    height: TICKER_HEIGHT,
    active: false,
    skin: new Skin({ fill: '#17212b' }),
    contents: [tickerLabel],
  })
}

export function setTicker(robot, message) {
  const text = String(message ?? '').trim()
  if (!text) {
    if (tickerEffect !== undefined) {
      robot.renderer.removeDecorator(tickerEffect)
    }
    tickerEffect = undefined
    tickerLabel = undefined
    state.ticker = ''
    return { ok: true, text: 'ok ticker off\n' }
  }
  if (probabilityBarEffect !== undefined) {
    robot.renderer.removeDecorator(probabilityBarEffect)
    probabilityBarEffect = undefined
    state.probabilityBar.visible = false
  }
  state.ticker = text
  if (tickerEffect === undefined) {
    tickerEffect = createTickerEffect(text)
    robot.renderer.addDecorator(tickerEffect)
  } else if (tickerLabel !== undefined) {
    tickerLabel.string = text
  }
  keepSetupQrOnTop(robot)
  return { ok: true, text: 'ok ticker\n' }
}

// ---------------------------------------------------------------------------
// Two-team probability bar (PK bar) with flags.

let probabilityBarEffect

function layoutProbabilitySide(segmentX, segmentWidth, flag, value, percent, leftSide) {
  const edge = 4
  const gap = 2
  const valueWidth = percent >= 100 ? 40 : percent >= 10 ? 30 : 18
  const groupWidth = FLAG_WIDTH + gap + valueWidth
  const showFlag = segmentWidth >= groupWidth + edge * 2

  flag.visible = showFlag
  value.visible = segmentWidth >= 16
  if (!value.visible) return 'center'

  if (!showFlag) {
    value.x = segmentX + 1
    value.width = Math.max(0, segmentWidth - 2)
    return 'center'
  }

  flag.width = FLAG_WIDTH
  if (leftSide) {
    flag.x = segmentX + edge
    value.x = flag.x + FLAG_WIDTH + gap
    value.width = valueWidth
    return 'left'
  }

  flag.x = segmentX + segmentWidth - edge - FLAG_WIDTH
  value.x = flag.x - gap - valueWidth
  value.width = valueWidth
  return 'right'
}

function flagSkin(code) {
  return new Skin({
    texture: { path: `flag-${code}.png` },
    x: 0,
    y: 0,
    width: FLAG_WIDTH,
    height: FLAG_HEIGHT,
  })
}

class ProbabilityBarBehavior extends Behavior {
  data

  onCreate(container, data) {
    this.data = data
    this.update(container)
  }

  onDisplaying(container) {
    this.update(container)
  }

  onAdapt(container) {
    this.update(container)
  }

  onProbabilityBar(container, data) {
    this.data = data
    this.update(container)
  }

  update(container) {
    if (!this.data) return
    const width = container.width || 320
    const leftPercent = clamp(Math.round(toNumber(this.data.leftPercent, 50)), 0, 100)
    const leftWidth = Math.round((width * leftPercent) / 100)
    const rightWidth = Math.max(0, width - leftWidth)
    const leftFill = container.content('pkLeftFill')
    const rightFill = container.content('pkRightFill')
    const divider = container.content('pkDivider')
    const leftFlag = container.content('pkLeftFlag')
    const rightFlag = container.content('pkRightFlag')
    const leftValue = container.content('pkLeftValue')
    const rightValue = container.content('pkRightValue')

    leftFill.x = 0
    leftFill.width = leftWidth
    leftFill.skin = new Skin({ fill: this.data.leftColor })
    rightFill.x = leftWidth
    rightFill.width = rightWidth
    rightFill.skin = new Skin({ fill: this.data.rightColor })
    divider.x = clamp(leftWidth - 1, 0, Math.max(0, width - 2))
    leftFlag.skin = flagSkin(this.data.leftFlag)
    rightFlag.skin = flagSkin(this.data.rightFlag)
    const rightPercent = 100 - leftPercent
    leftValue.string = String(leftPercent)
    rightValue.string = String(rightPercent)
    const leftHorizontal = layoutProbabilitySide(0, leftWidth, leftFlag, leftValue, leftPercent, true)
    const rightHorizontal = layoutProbabilitySide(leftWidth, rightWidth, rightFlag, rightValue, rightPercent, false)
    leftValue.style = new Style({
      font: FONT,
      color: contrastColor(this.data.leftColor),
      horizontal: leftHorizontal,
      vertical: 'middle',
    })
    rightValue.style = new Style({
      font: FONT,
      color: contrastColor(this.data.rightColor),
      horizontal: rightHorizontal,
      vertical: 'middle',
    })
  }
}

function createProbabilityBarEffect(data) {
  return new Container(data, {
    name: 'ProbabilityBar',
    left: 0,
    right: 0,
    // The host owns y=0..18 for its status bar and the face starts at y=54.
    // This strip fits between them; speech balloons stay at the lower edge.
    top: PK_BAR_TOP,
    height: PK_BAR_HEIGHT,
    active: false,
    contents: [
      new Content(null, { name: 'pkLeftFill', left: 0, top: 0, bottom: 0, width: 160 }),
      new Content(null, { name: 'pkRightFill', left: 160, top: 0, bottom: 0, width: 160 }),
      new Content(null, {
        name: 'pkDivider',
        left: 159,
        top: 0,
        bottom: 0,
        width: 2,
        skin: new Skin({ fill: '#ffffff' }),
      }),
      new Content(null, { name: 'pkLeftFlag', left: 0, top: 8, width: FLAG_WIDTH, height: FLAG_HEIGHT }),
      new Label(null, { name: 'pkLeftValue', left: 0, top: 0, bottom: 0, width: 30, string: '50' }),
      new Content(null, { name: 'pkRightFlag', left: 0, top: 8, width: FLAG_WIDTH, height: FLAG_HEIGHT }),
      new Label(null, { name: 'pkRightValue', left: 0, top: 0, bottom: 0, width: 30, string: '50' }),
    ],
    Behavior: ProbabilityBarBehavior,
  })
}

export function hideProbabilityBar(robot) {
  if (probabilityBarEffect !== undefined) {
    robot.renderer.removeDecorator(probabilityBarEffect)
  }
  probabilityBarEffect = undefined
  state.probabilityBar.visible = false
  return { ok: true, text: 'ok pkbar off\n' }
}

export function setProbabilityBar(robot, data) {
  const leftPercent = clamp(Math.round(toNumber(data.leftPercent, 50)), 0, 100)
  const next = {
    visible: true,
    position: 'top',
    leftFlag: String(data.leftFlag ?? '')
      .trim()
      .toLowerCase(),
    leftPercent,
    leftColor: normalizeHexColor(data.leftColor, '#2457a6'),
    rightFlag: String(data.rightFlag ?? '')
      .trim()
      .toLowerCase(),
    rightPercent: 100 - leftPercent,
    rightColor: normalizeHexColor(data.rightColor, '#c1272d'),
  }
  if (!/^[a-z]{2}(?:-[a-z]{3})?$/.test(next.leftFlag) || !/^[a-z]{2}(?:-[a-z]{3})?$/.test(next.rightFlag)) {
    return {
      ok: false,
      text: 'error pkbar flag codes must look like fr or gb-eng\n',
    }
  }
  if (tickerEffect !== undefined) {
    robot.renderer.removeDecorator(tickerEffect)
    tickerEffect = undefined
    tickerLabel = undefined
    state.ticker = ''
  }
  state.probabilityBar = next
  if (probabilityBarEffect === undefined) {
    probabilityBarEffect = createProbabilityBarEffect(next)
    robot.renderer.addDecorator(probabilityBarEffect)
  } else {
    probabilityBarEffect.behavior?.onProbabilityBar?.(probabilityBarEffect, next)
  }
  keepSetupQrOnTop(robot)
  return { ok: true, text: `ok pkbar ${leftPercent} ${100 - leftPercent}\n` }
}

// ---------------------------------------------------------------------------
// Full-screen QR overlay for the phone setup page.

let setupEffect
let setupRestoreTimer

function keepSetupQrOnTop(robot) {
  if (setupEffect === undefined) return
  robot.renderer.removeDecorator(setupEffect)
  robot.renderer.addDecorator(setupEffect)
}

const SETUP_QR_TITLE = {
  zh: '手机扫码设置比赛',
  en: 'Scan to set up the match',
}

// Re-render the visible QR overlay (e.g. after a language switch) without
// resetting its 90-second auto-hide timer.
export function refreshSetupQr(robot) {
  if (setupEffect === undefined) return
  const url = state.setup.url
  robot.renderer.removeDecorator(setupEffect)
  setupEffect = createSetupQrEffect(url)
  robot.renderer.addDecorator(setupEffect)
}

function createSetupQrEffect(url) {
  const shortUrl = url.replace(/^https?:\/\//, '')
  const title = SETUP_QR_TITLE[state.matchSetup.language] ?? SETUP_QR_TITLE.zh
  const qrTexture = new Texture('setup-qr.png')
  const qrWidth = qrTexture.width
  const qrHeight = qrTexture.height
  const qrLeft = Math.round((320 - qrWidth) / 2)
  const qrTop = 35 + Math.max(0, Math.round((168 - qrHeight) / 2))
  return new Container(null, {
    name: 'MatchSetupQr',
    left: 0,
    right: 0,
    top: 0,
    bottom: 0,
    active: false,
    skin: new Skin({ fill: '#ffffff' }),
    contents: [
      new Label(null, {
        left: 8,
        right: 8,
        top: 5,
        height: 28,
        string: title,
        style: new Style({ font: FONT, color: '#17202a', horizontal: 'center', vertical: 'middle' }),
      }),
      new Content(null, {
        left: qrLeft,
        top: qrTop,
        width: qrWidth,
        height: qrHeight,
        skin: new Skin({
          texture: qrTexture,
          x: 0,
          y: 0,
          width: qrWidth,
          height: qrHeight,
        }),
      }),
      new Label(null, {
        left: 8,
        right: 8,
        bottom: 5,
        height: 28,
        string: shortUrl,
        style: new Style({ font: FONT, color: '#425466', horizontal: 'center', vertical: 'middle' }),
      }),
    ],
  })
}

export function hideSetupQr(robot) {
  if (setupRestoreTimer !== undefined) {
    Timer.clear(setupRestoreTimer)
    setupRestoreTimer = undefined
  }
  if (setupEffect !== undefined) {
    robot.renderer.removeDecorator(setupEffect)
    setupEffect = undefined
  }
  state.setup.visible = false
  state.setup.url = ''
  return { ok: true, text: 'ok setup hidden\n' }
}

export function showSetupQr(robot, value) {
  const url = String(value ?? '').trim()
  if (!/^https?:\/\/[0-9A-Za-z_.:-]+(?:\/[^\s]*)?$/.test(url)) {
    return { ok: false, text: 'error usage: setup show <http-url>\n' }
  }
  hideSetupQr(robot)
  setupEffect = createSetupQrEffect(url)
  robot.renderer.addDecorator(setupEffect)
  state.setup.visible = true
  state.setup.url = url
  setupRestoreTimer = Timer.set(() => {
    setupRestoreTimer = undefined
    hideSetupQr(robot)
  }, 90000)
  return { ok: true, text: `ok setup ${url}\n` }
}

// ---------------------------------------------------------------------------
// Screen brightness, auto-dim power management, idle look.

let powerTimer
let idleLookTimer

export function setScreenBrightness(value) {
  const brightness = Math.round(clamp(toNumber(value, state.screen.brightness), 0, 100))
  const backlight = globalThis.backlight
  if (backlight?.write) {
    backlight.write(brightness)
  } else if (globalThis.power) {
    globalThis.power.brightness = brightness
  } else {
    return { ok: false, text: 'error screen brightness unsupported\n' }
  }
  state.screen.brightness = brightness
  return { ok: true, text: `ok screen brightness ${brightness}\n` }
}

export function setUserBrightness(value) {
  const result = setScreenBrightness(value)
  if (result.ok) {
    state.power.dimmed = false
    state.power.wakeBrightness = state.screen.brightness
    savePreference('wakeBright', state.power.wakeBrightness)
  }
  return result
}

export function noteActivity(reason = 'activity') {
  state.power.lastActivityTicks = Time.ticks
  state.power.lastActivity = reason
  if (state.power.autoDim && state.power.dimmed) {
    setScreenBrightness(state.power.wakeBrightness)
    state.power.dimmed = false
  }
}

export function startPowerManager() {
  if (powerTimer !== undefined) {
    Timer.clear(powerTimer)
  }
  if (!state.power.lastActivityTicks) {
    state.power.lastActivityTicks = Time.ticks
  }
  powerTimer = Timer.repeat(() => {
    if (!state.power.autoDim || state.power.dimmed) return
    if (elapsedSince(state.power.lastActivityTicks) < state.power.idleMs) return
    const result = setScreenBrightness(state.power.dimBrightness)
    if (result.ok) {
      state.power.dimmed = true
    }
  }, POWER_CHECK_INTERVAL_MS)
}

function randomLookVector() {
  return [randomBetween(0.4, 1.0), randomBetween(-0.4, 0.4), randomBetween(-0.02, 0.2)]
}

export function stopIdleLook(robot, releaseGaze = true) {
  if (idleLookTimer !== undefined) {
    Timer.clear(idleLookTimer)
    idleLookTimer = undefined
  }
  state.idle.look = false
  if (releaseGaze) {
    robot.lookAway()
    state.gaze = null
  }
}

export function startIdleLook(robot, intervalMs) {
  stopIdleLook(robot, false)
  state.idle.look = true
  state.idle.intervalMs = clamp(Math.round(intervalMs), MIN_IDLE_LOOK_INTERVAL_MS, MAX_IDLE_LOOK_INTERVAL_MS)
  const update = () => {
    const gaze = randomLookVector()
    robot.lookAt(gaze)
    state.gaze = gaze
  }
  update()
  idleLookTimer = Timer.repeat(update, state.idle.intervalMs)
}
