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

context.state.matchSetup.current = { label: 'keep me' }
const optionCount = context.state.matchSetup.options.length
context.syncMatchSetup({ commentary_style: 'professional' })
assert.equal(context.state.matchSetup.commentaryStyle, 'professional')
assert.equal(context.state.matchSetup.options.length, optionCount)
assert.equal(context.state.matchSetup.current.label, 'keep me')

console.log('ok mod match-setup style relay behavior')
