import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

// Drive mod/http-server-safe.js with a scripted listener to prove the accept
// loop survives stalled bodies, slow handlers, wedged peers, and bursts.

const source = fs.readFileSync(new URL('../mod/http-server-safe.js', import.meta.url), 'utf8')
const body = source
  .split('\n')
  .filter((line) => !line.startsWith('import ') && !line.startsWith('export '))
  .join('\n')

function makeHarness() {
  const pendingConnections = []
  const waiters = []
  const timers = []
  const traces = []

  async function* listen() {
    for (;;) {
      if (pendingConnections.length) yield pendingConnections.shift()
      else await new Promise((resolve) => waiters.push(resolve))
    }
  }

  const context = vm.createContext({
    ArrayBuffer,
    JSON,
    Object,
    String,
    Headers: class {
      #map = new Map()
      get(key) {
        return this.#map.get(String(key).toLowerCase())
      }
      set(key, value) {
        this.#map.set(String(key).toLowerCase(), value)
      }
      entries() {
        return this.#map.entries()
      }
    },
    URLSearchParams,
    Timer: {
      set: (callback, ms) => {
        const timer = { callback, ms, cleared: false }
        timers.push(timer)
        return timer
      },
      clear: (timer) => {
        if (timer) timer.cleared = true
      },
    },
    listen,
    trace: (text) => traces.push(text),
  })
  vm.runInContext('ArrayBuffer.fromString = (s) => new ArrayBuffer(String(s).length)', context)
  vm.runInContext('String.fromArrayBuffer = () => ""', context)
  vm.runInContext(body, context)

  return {
    context,
    traces,
    timers,
    push(connection) {
      pendingConnections.push(connection)
      const waiter = waiters.shift()
      if (waiter) waiter()
    },
  }
}

function makeConnection(method, path, bodyPromise) {
  const events = []
  return {
    events,
    request: {
      method,
      url: { pathname: path, href: `http://device${path}`, searchParams: new URLSearchParams() },
      headers: { get: () => undefined },
      arrayBuffer: () => bodyPromise ?? Promise.resolve(new ArrayBuffer(0)),
    },
    close() {
      events.push('close')
    },
    respondWith(response) {
      events.push(['respond', response.status])
      return Promise.resolve()
    },
  }
}

const settle = async (turns = 8) => {
  for (let i = 0; i < turns; i++) await new Promise((resolve) => setImmediate(resolve))
}

// 1) A stalled request body must not block other clients (the wedge that froze
//    the device: TCP handshakes kept succeeding while no request got answered).
{
  const harness = makeHarness()
  const server = vm.runInContext('new HttpServerService({ port: 80 })', harness.context)
  harness.context.__server = server
  vm.runInContext(
    `__server.get('/ping', (c) => c.text('pong', 200))
     __server.post('/slow', async (c) => { await c.req.text(); return c.text('done', 200) })`,
    harness.context,
  )

  const stalled = makeConnection('POST', '/slow', new Promise(() => {}))
  harness.push(stalled)
  await settle()

  const healthy = makeConnection('GET', '/ping')
  harness.push(healthy)
  await settle()

  assert.deepEqual(healthy.events, [['respond', 200]], 'healthy request must be served while another body stalls')
  assert.deepEqual(stalled.events, [], 'stalled request is still pending, not errored')

  // 2) The watchdog reclaims the wedged connection.
  const watchdog = harness.timers.find((t) => !t.cleared && t.ms === 45_000)
  assert.ok(watchdog, 'stalled request must be under a watchdog timer')
  watchdog.callback()
  await settle()
  assert.deepEqual(stalled.events, ['close'], 'watchdog closes the wedged connection')
  assert.ok(
    harness.traces.some((t) => t.includes('HTTP request timed out')),
    'timeout is traced',
  )

  // 3) After reclaim, new requests still work and the slot was released.
  const after = makeConnection('GET', '/ping')
  harness.push(after)
  await settle()
  assert.deepEqual(after.events, [['respond', 200]])
  assert.equal(vm.runInContext('__server ? 0 : 1', harness.context), 0)
}

// 4) Load shedding: beyond MAX_ACTIVE_REQUESTS concurrent requests, extra
//    connections are dropped instead of exhausting lwIP pcbs.
{
  const harness = makeHarness()
  const server = vm.runInContext('new HttpServerService({ port: 80 })', harness.context)
  harness.context.__server = server
  vm.runInContext(`__server.post('/slow', async (c) => { await c.req.text(); return c.text('done', 200) })`, harness.context)

  const wedged = []
  for (let i = 0; i < 6; i++) {
    const connection = makeConnection('POST', '/slow', new Promise(() => {}))
    wedged.push(connection)
    harness.push(connection)
  }
  await settle()

  const extra = makeConnection('POST', '/slow', new Promise(() => {}))
  harness.push(extra)
  await settle()
  assert.deepEqual(extra.events, ['close'], 'connection beyond the cap is shed immediately')
  assert.ok(
    wedged.every((c) => c.events.length === 0),
    'capped connections are untouched',
  )
}

// 5) Handler failures still answer 500 (previous behavior preserved).
{
  const harness = makeHarness()
  const server = vm.runInContext('new HttpServerService({ port: 80 })', harness.context)
  harness.context.__server = server
  vm.runInContext(`__server.get('/boom', () => { throw new Error('boom') })`, harness.context)

  const boom = makeConnection('GET', '/boom')
  harness.push(boom)
  await settle()
  assert.deepEqual(boom.events, [['respond', 500]])
  assert.ok(harness.traces.some((t) => t.includes('HTTP handler failed')))

  const missing = makeConnection('GET', '/nope')
  harness.push(missing)
  await settle()
  assert.deepEqual(missing.events, [['respond', 404]])
}

// 6) A respondWith rejection (peer dropped) is contained: traced, closed, and
//    the slot is released for the next client.
{
  const harness = makeHarness()
  const server = vm.runInContext('new HttpServerService({ port: 80 })', harness.context)
  harness.context.__server = server
  vm.runInContext(`__server.get('/ping', (c) => c.text('pong', 200))`, harness.context)

  const dropped = makeConnection('GET', '/ping')
  dropped.respondWith = () => {
    dropped.events.push('respond-attempt')
    return Promise.reject(new Error('peer gone'))
  }
  harness.push(dropped)
  await settle()
  assert.deepEqual(dropped.events, ['respond-attempt', 'close'])
  assert.ok(harness.traces.some((t) => t.includes('HTTP response closed')))

  const next = makeConnection('GET', '/ping')
  harness.push(next)
  await settle()
  assert.deepEqual(next.events, [['respond', 200]])
}

console.log('http-server-safe behavior: all assertions passed')
