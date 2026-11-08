import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFile } from 'node:fs/promises'

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

async function serviceWorkerHarness({ networkResponse, put = async () => {}, cachedResponse = null }) {
  const source = await readFile(new URL('../public/sw.js', import.meta.url), 'utf8')
  const listeners = {}
  const putCalls = []
  const cache = {
    addAll: async () => {},
    put: async (...args) => { putCalls.push(args); return put(...args) },
  }
  const context = {
    URL,
    Promise,
    console,
    fetch: async () => networkResponse,
    caches: {
      open: async () => cache,
      match: async () => cachedResponse,
      keys: async () => [],
      delete: async () => true,
    },
    self: {
      location: { origin: 'https://example.test' },
      addEventListener: (name, handler) => { listeners[name] = handler },
      skipWaiting: async () => {},
      clients: { claim: async () => {} },
    },
  }
  vm.runInNewContext(source, context, { filename: 'sw.js' })
  return {
    putCalls,
    dispatch(request) {
      let responsePromise
      const waits = []
      listeners.fetch({
        request,
        respondWith: promise => { responsePromise = Promise.resolve(promise) },
        waitUntil: promise => { waits.push(Promise.resolve(promise)) },
      })
      return { responsePromise, waits }
    },
  }
}

test('same-origin successful data fetch stays alive until cache.put completes', async () => {
  const putGate = deferred()
  const networkResponse = { ok: true, status: 200, clone() { return { ...this } } }
  const harness = await serviceWorkerHarness({ networkResponse, put: () => putGate.promise })
  const request = { method: 'GET', url: 'https://example.test/a-share-finance/data/funds/events.json' }
  const { responsePromise } = harness.dispatch(request)
  let settled = false
  responsePromise.then(() => { settled = true })

  await new Promise(resolve => setImmediate(resolve))
  assert.equal(settled, false)
  assert.equal(harness.putCalls.length, 1)

  putGate.resolve()
  assert.equal(await responsePromise, networkResponse)
})

test('failed and cross-origin data responses never replace the dynamic cache', async () => {
  const cachedResponse = { ok: true, status: 200, cached: true }
  for (const [url, networkResponse, cached] of [
    ['https://example.test/a-share-finance/data/funds/events.json', { ok: false, status: 500, clone() { return { ...this } } }, cachedResponse],
    ['https://other.test/data/funds/events.json', { ok: true, status: 200, clone() { return { ...this } } }, null],
  ]) {
    const harness = await serviceWorkerHarness({ networkResponse, cachedResponse: cached })
    const { responsePromise } = harness.dispatch({ method: 'GET', url })
    assert.equal(await responsePromise, networkResponse)
    assert.equal(harness.putCalls.length, 0)
  }
})
