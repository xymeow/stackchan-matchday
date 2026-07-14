import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

const source = fs.readFileSync(new URL('../mod/web.js', import.meta.url), 'utf8')
const relaySource = source.slice(
  source.indexOf('function applyLanguage'),
  source.indexOf('// Pages.'),
)

const preferences = new Map()
const context = vm.createContext({
  ArrayBuffer,
  COMMENTARY_STYLES: ['casual', 'balanced', 'professional'],
  JSON,
  Response: class {},
  nowTicks: () => 123,
  readPreference: (key, fallback) => preferences.has(key) ? preferences.get(key) : fallback,
  savePreference: (key, value) => {
    if (value === undefined || value === null) preferences.delete(key)
    else preferences.set(key, value)
  },
  state: {
    matchSetup: {
      language: 'zh',
      commentaryStyle: 'balanced',
      spoilerFreeMode: false,
      options: [{
        event_id: '760511',
        kalshi_event_ticker: 'KXWCADVANCE-26JUL10ESPBEL',
        home: { name: 'Spain' },
        away: { name: 'Belgium' },
      }],
      current: {},
      pending: null,
      lastResult: null,
    },
  },
})
vm.runInContext(relaySource, context)

const queuedStyle = context.queueCommentaryStyle({
  request_id: 'style-1',
  commentary_style: 'professional',
})
assert.equal(queuedStyle.ok, true)
assert.equal(context.state.matchSetup.commentaryStyle, 'balanced')
assert.equal(context.state.matchSetup.pending.commentary_style, 'professional')

// A legacy full-setup client may omit commentary_style. It must absorb the
// pending style preference instead of reverting to the old effective value.
const queuedMatch = context.queueMatchSetup({
  request_id: 'match-1',
  event_ticker: 'KXWCADVANCE-26JUL10ESPBEL',
  espn_event_id: '760511',
  favorite_team: 'Spain',
  position_team: '',
  language: 'en',
})
assert.equal(queuedMatch.ok, true)
assert.equal(context.state.matchSetup.pending.request_id, 'match-1')
assert.equal(context.state.matchSetup.pending.commentary_style, 'professional')

const collision = context.queueCommentaryStyle({
  request_id: 'style-2',
  commentary_style: 'casual',
})
assert.equal(collision.ok, false)
assert.equal(collision.status, 409)
assert.equal(context.state.matchSetup.pending.request_id, 'match-1')

assert.equal(context.acknowledgeMatchSetup({ ok: true }).ok, false)
assert.equal(context.acknowledgeMatchSetup({ request_id: 'stale', ok: true }).ok, false)
assert.equal(context.state.matchSetup.pending.request_id, 'match-1')

const acknowledged = context.acknowledgeMatchSetup({
  request_id: 'match-1',
  ok: true,
  language: 'en',
  commentary_style: 'professional',
})
assert.equal(acknowledged.ok, true)
assert.equal(context.state.matchSetup.pending, null)
assert.equal(context.state.matchSetup.commentaryStyle, 'professional')
assert.equal(preferences.get('commentaryStyle'), 'professional')

const staleAfterCompletion = context.acknowledgeMatchSetup({
  request_id: 'match-1',
  ok: true,
  commentary_style: 'casual',
})
assert.equal(staleAfterCompletion.ok, false)
assert.equal(context.state.matchSetup.commentaryStyle, 'professional')

assert.equal(context.queueCommentaryStyle({ commentary_style: 'dramatic' }).status, 400)
const queuedSecondStyle = context.queueCommentaryStyle({
  request_id: 'style-3',
  commentary_style: 'casual',
})
assert.equal(queuedSecondStyle.ok, true)
assert.equal(context.state.matchSetup.commentaryStyle, 'professional')
const styleAcknowledged = context.acknowledgeMatchSetup({
  request_id: 'style-3',
  ok: true,
  commentary_style: 'casual',
})
assert.equal(styleAcknowledged.ok, true)
assert.equal(context.state.matchSetup.commentaryStyle, 'casual')
assert.equal(context.state.matchSetup.lastResult.style_only, true)

assert.equal(context.queueSpoilerFreeMode({ spoiler_free_mode: 'true' }).status, 400)
const queuedSpoiler = context.queueSpoilerFreeMode({
  request_id: 'spoiler-1',
  spoiler_free_mode: true,
})
assert.equal(queuedSpoiler.ok, true)
assert.equal(context.state.matchSetup.spoilerFreeMode, false)
assert.equal(context.state.matchSetup.pending.spoiler_only, true)
assert.equal(context.state.matchSetup.pending.spoiler_free_mode, true)

// A user can toggle spoiler protection and immediately submit the fixture.
// The full request must absorb the pending choice instead of reverting it.
const queuedSpoilerMatch = context.queueMatchSetup({
  request_id: 'match-2',
  event_ticker: 'KXWCADVANCE-26JUL10ESPBEL',
  espn_event_id: '760511',
  favorite_team: 'Belgium',
  position_team: 'Belgium',
  language: 'zh',
})
assert.equal(queuedSpoilerMatch.ok, true)
assert.equal(context.state.matchSetup.spoilerFreeMode, false)
assert.equal(context.state.matchSetup.pending.request_id, 'match-2')
assert.equal(context.state.matchSetup.pending.spoiler_free_mode, true)

const spoilerMatchAcknowledged = context.acknowledgeMatchSetup({
  request_id: 'match-2',
  ok: true,
  language: 'zh',
  commentary_style: 'casual',
  spoiler_free_mode: true,
})
assert.equal(spoilerMatchAcknowledged.ok, true)
assert.equal(context.state.matchSetup.spoilerFreeMode, true)
assert.equal(preferences.get('spoilerFreeMode'), true)

const queuedSpoilerOff = context.queueSpoilerFreeMode({
  request_id: 'spoiler-2',
  spoiler_free_mode: false,
})
assert.equal(queuedSpoilerOff.ok, true)
assert.equal(context.state.matchSetup.spoilerFreeMode, true)
assert.equal(context.queueCommentaryStyle({ commentary_style: 'balanced' }).status, 409)
const spoilerAcknowledged = context.acknowledgeMatchSetup({
  request_id: 'spoiler-2',
  ok: true,
  spoiler_free_mode: false,
})
assert.equal(spoilerAcknowledged.ok, true)
assert.equal(context.state.matchSetup.spoilerFreeMode, false)
assert.equal(context.state.matchSetup.lastResult.spoiler_only, true)

const queuedStandalone = context.queueMatchSetup({
  request_id: 'market-1',
  standalone: true,
  kalshi_url: 'KXWCADVANCE-26JUL10ESPBEL',
  language: 'en',
  commentary_style: 'professional',
  spoiler_free_mode: true,
})
assert.equal(queuedStandalone.ok, true)
assert.equal(context.state.matchSetup.pending.spoiler_free_mode, true)
assert.equal(context.acknowledgeMatchSetup({
  request_id: 'market-1',
  ok: true,
  spoiler_free_mode: true,
}).ok, true)
assert.equal(context.state.matchSetup.spoilerFreeMode, true)

context.state.matchSetup.current = { label: 'keep me' }
const optionCount = context.state.matchSetup.options.length
context.syncMatchSetup({ commentary_style: 'professional', spoiler_free_mode: false })
assert.equal(context.state.matchSetup.commentaryStyle, 'professional')
assert.equal(context.state.matchSetup.spoilerFreeMode, false)
assert.equal(context.state.matchSetup.options.length, optionCount)
assert.equal(context.state.matchSetup.current.label, 'keep me')

console.log('ok mod match-setup preference relay behavior')
