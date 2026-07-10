// Compatibility HTTP service for older Stack-chan hosts.
//
// The bundled service in those hosts can throw while constructing a text 404,
// and it leaves respondWith() rejections unhandled when a client disconnects.
// Either failure aborts the complete XS runtime, including the touch handlers.
import Headers from 'headers'
import listen from 'listen'
import { URLSearchParams } from 'url'

class Request {
  raw
  #bodyResult

  constructor(request) {
    this.raw = request
    // Always observe the listener's request-body promise. A GET handler does
    // not read its body, but the old listener still rejects that promise when
    // the peer disconnects; leaving it unobserved aborts XS.
    this.#bodyResult = request.arrayBuffer().then(
      (body) => ({ body }),
      (error) => ({ error }),
    )
  }

  get method() {
    return this.raw.method.toLowerCase()
  }

  get path() {
    return this.raw.url.pathname
  }

  get url() {
    return this.raw.url.href
  }

  header(key) {
    return this.raw.headers.get(key.toLowerCase())
  }

  query(key) {
    return key ? this.raw.url.searchParams.get(key) : Object.fromEntries(this.raw.url.searchParams.entries())
  }

  async arrayBuffer() {
    const result = await this.#bodyResult
    if (result.error !== undefined) throw result.error
    return result.body
  }

  async text() {
    const body = await this.arrayBuffer()
    return body ? String.fromArrayBuffer(body) : ''
  }

  async json() {
    const text = await this.text()
    return text ? JSON.parse(text) : undefined
  }

  async formData() {
    return Object.fromEntries(new URLSearchParams(await this.text()))
  }
}

class Response {
  #body
  #headers
  #status

  constructor(body, options = {}) {
    this.#body = body instanceof ArrayBuffer ? body : ArrayBuffer.fromString(String(body ?? ''))

    const headers = new Headers()
    if (options.headers) {
      for (const [key, value] of Object.entries(options.headers)) {
        if (value !== undefined && value !== null) headers.set(key, value)
      }
    }
    if (headers.get('content-length') === undefined) {
      headers.set('content-length', this.#body.byteLength)
    }

    this.#headers = headers
    this.#status = options.status ?? 200
  }

  get body() {
    return this.#body
  }

  get headers() {
    return this.#headers
  }

  get status() {
    return this.#status
  }

  async arrayBuffer() {
    let body = this.#body
    if (body) {
      this.#body = undefined
      body = await body
    }
    return body
  }

  async json() {
    let body = await this.arrayBuffer()
    if (body) {
      body = String.fromArrayBuffer(body)
      return JSON.parse(body)
    }
    return body
  }

  async text() {
    let body = await this.arrayBuffer()
    if (body) body = String.fromArrayBuffer(body)
    return body
  }
}

class Context {
  #req
  #status
  #headers = new Headers()

  constructor(request) {
    this.#req = new Request(request)
  }

  get req() {
    return this.#req
  }

  status(status) {
    this.#status = status
  }

  header(key, value) {
    this.#headers.set(key, value)
  }

  text(value, status) {
    this.#headers.set('Content-Type', 'text/plain')
    return new Response(value, {
      status: status ?? this.#status,
      headers: Object.fromEntries(this.#headers.entries()),
    })
  }

  json(value, status) {
    this.#headers.set('Content-Type', 'application/json')
    return new Response(JSON.stringify(value), {
      status: status ?? this.#status,
      headers: Object.fromEntries(this.#headers.entries()),
    })
  }
}

class HttpServerService {
  #routes = {
    get: new Map(),
    post: new Map(),
    put: new Map(),
    patch: new Map(),
    delete: new Map(),
    options: new Map(),
  }

  get = (path, handler) => this.#routes.get.set(path, handler)
  post = (path, handler) => this.#routes.post.set(path, handler)
  put = (path, handler) => this.#routes.put.set(path, handler)
  patch = (path, handler) => this.#routes.patch.set(path, handler)
  delete = (path, handler) => this.#routes.delete.set(path, handler)
  options = (path, handler) => this.#routes.options.set(path, handler)

  constructor(options = {}) {
    this.#listen(options.port).catch((error) => {
      trace(`[matchday] HTTP listener stopped: ${error}\n`)
    })
  }

  async #listen(port) {
    for await (const connection of listen({ port })) {
      let context
      let response

      try {
        context = new Context(connection.request)
        const routes = this.#routes[context.req.method]
        const handler = routes ? routes.get(context.req.path) : undefined
        response = handler ? await handler(context) : context.text('Resource Not Found', 404)
        if (!response) response = context.text('Empty Response', 500)
      } catch (error) {
        trace(`[matchday] HTTP handler failed: ${error}\n`)
        response = context ? context.text('Internal Server Error', 500) : new Response('Internal Server Error', { status: 500 })
      }

      // Keep accepting queued requests while the response is written, but
      // attach the rejection handler synchronously so a dropped phone/browser
      // connection can never become an unhandled promise rejection.
      connection.respondWith(response).catch((error) => {
        trace(`[matchday] HTTP response closed: ${error}\n`)
        try {
          connection.close()
        } catch (_closeError) {}
      })
    }
  }
}

export { HttpServerService, Response }
