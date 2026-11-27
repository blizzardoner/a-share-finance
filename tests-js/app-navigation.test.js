import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFile } from 'node:fs/promises'
import * as lib from '../public/lib.js'

const tick = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => { let resolve; let reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

// Small DOM/History adapter: execute the real app, dispatch its handlers, and
// assert rendered output. No source-text assertions or alternate navigation code.
async function harness(search = '', { companyError = false, initialEntry = null } = {}) {
  const html = await readFile(new URL('../public/index.html', import.meta.url), 'utf8')
  const nodes = new Map(), listeners = {}, requests = []
  function node(id, dataset = {}, active = false) {
    const classes = new Set(active ? ['active'] : [])
    const handlers = {}
    return { id, dataset, value: '', hidden: false, disabled: false, innerHTML: '', textContent: '', offsetTop: 600,
      classList: { contains: x => classes.has(x), toggle: (x, on = !classes.has(x)) => on ? classes.add(x) : classes.delete(x), add: x => classes.add(x), remove: x => classes.delete(x) },
      addEventListener: (name, fn) => { handlers[name] = fn },
      querySelectorAll() {
        if (this.renderedHtml !== this.innerHTML) {
          this.renderedHtml = this.innerHTML
          this.children = [...this.innerHTML.matchAll(/data-code="([^"]+)"/g)].map(m => node('', { code: m[1] }))
        }
        return this.children || []
      },
      closest(selector) { return selector.includes('data-') || selector === '.search-wrap' ? this : null },
      dispatch(name) { return (handlers[name] || this['on' + name])?.({ target: this }) },
      handlers,
    }
  }
  for (const match of html.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const el = node(match[1]); el.hidden = /\bhidden\b/.test(match[0]); el.disabled = /\bdisabled\b/.test(match[0]); nodes.set(el.id, el)
  }
  const buttons = []
  for (const match of html.matchAll(/<button([^>]*data-(tab|ranking-kind|ranking-years|fund-mode)="([^"]+)"[^>]*)>/g)) {
    const key = match[2].replace(/-([a-z])/g, (_, x) => x.toUpperCase())
    buttons.push(node('', { [key]: match[3] }, /class="active"/.test(match[1])))
  }
  const panels = [...nodes.values()].filter(x => x.id.startsWith('panel-'))
  panels[0].classList.add('active')
  const document = {
    getElementById: id => nodes.get(id),
    querySelectorAll(selector) {
      if (selector === '.tab-panel') return panels
      const key = selector === '#tabs button' ? 'tab' : selector.match(/data-([\w-]+)/)?.[1]?.replace(/-([a-z])/g, (_, x) => x.toUpperCase())
      return buttons.filter(x => key in x.dataset)
    },
    querySelector(selector) { if (selector === '.company-head') return { offsetTop: 600 }; return this.querySelectorAll(selector).find(x => !selector.includes('="') || x.dataset.tab === selector.match(/="([^"]+)"/)[1]) },
    addEventListener: (name, fn) => { listeners['document:' + name] = fn },
  }
  const location = { pathname: '/app/', search }
  let cursor = 0
  const entries = [initialEntry || { state: null, url: '/app/' + search }]
  function setLocation(url) { location.search = new URL(url, 'https://example.test').search }
  const history = {
    get state() { return structuredClone(entries[cursor].state) },
    replaceState(state, _, url) { entries[cursor] = { state: structuredClone(state), url }; setLocation(url) },
    pushState(state, _, url) { entries.splice(++cursor); entries.push({ state: structuredClone(state), url }); setLocation(url) },
    back() { if (cursor) { cursor--; setLocation(entries[cursor].url); listeners.popstate?.({ state: this.state }) } },
    forward() { if (cursor + 1 < entries.length) { cursor++; setLocation(entries[cursor].url); listeners.popstate?.({ state: this.state }) } },
  }
  const stocks = Array.from({ length: 220 }, (_, i) => ({ code: String(600000 + i), name: '测试公司' + i, exchange: 'SH', industry: '测试', market_cap: 2e10 }))
  const company = code => ({ stock: { ...stocks.find(x => x.code === code), secucode: code + '.SH' }, financials: [], dividends: [] })
  const gates = new Map()
  const window = { listeners, scrollY: 0, scrollX: 0, addEventListener: (name, fn) => { listeners[name] = fn }, scrollTo({ top }) { this.scrollY = top; listeners.scroll?.() }, requestAnimationFrame: fn => fn() }
  const context = { ...lib, document, window, history, location, navigator: {}, URLSearchParams, URL, console, setTimeout, clearTimeout, requestAnimationFrame: fn => fn(), structuredClone,
    fetch: async url => {
      requests.push(url)
      if (gates.has(url)) return gates.get(url).promise
      let payload
      if (url.endsWith('stocks.json')) payload = { stocks }
      else if (url.includes('/companies/')) { if (companyError) return { ok: false, status: 503 }; payload = company(url.match(/(\d+)\.json$/)[1]) }
      else if (url.includes('/rankings/')) payload = { meta: { end_year: 2025 }, rows: stocks.map((s, i) => ({ ...s, peg: 1, average_yield: 10 - i / 100, score: 10 - i / 100 })) }
      else if (url.endsWith('consensus.json')) payload = { rows: stocks.map(s => ({ ...s, consensus_score: 1, direction: 'positive' })) }
      else if (url.endsWith('universe.json')) payload = { funds: [] }
      else payload = { events: stocks.map(s => ({ stock_code: s.code, stock_name: s.name, event_type: 'CONFIRMED_NEW' })) }
      return { ok: true, json: async () => payload }
    },
  }
  const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
  vm.runInNewContext(source.replace(/^import \{[\s\S]*?\} from '.\/lib.js'\r?\n/, ''), context, { filename: 'app.js' })
  await tick()
  const click = async id => { await nodes.get(id).dispatch('click'); await tick() }
  const choose = async (id, key, value) => { await nodes.get(id).handlers.click({ target: buttons.find(x => x.dataset[key] === value) }); await tick() }
  const input = async (id, value) => { nodes.get(id).value = value; await nodes.get(id).dispatch('input'); listeners['document:input']?.({ target: nodes.get(id) }); await tick() }
  const stock = async (id, code) => { await nodes.get(id).handlers.click({ target: node('', { [id === 'ranking-list' ? 'rankingCode' : 'fundStock']: code }) }); await tick() }
  const searchStock = async code => { await nodes.get('search-results').querySelectorAll().find(x => x.dataset.code === code).onclick(); await tick() }
  return { nodes, window, history, location, requests, gates, company, click, choose, input, stock, searchStock, tick, entries }
}

test('stock Back restores ranking filters, loaded rows and scroll; browser Forward restores detail', async () => {
  const h = await harness()
  await h.click('ranking-open')
  await h.choose('ranking-types', 'rankingKind', 'peg')
  await h.choose('ranking-years', 'rankingYears', '5')
  for (const [id, value] of [['ranking-search', '测试'], ['ranking-cap-min', '100'], ['ranking-cap-max', '300'], ['ranking-peg-min', '0.5'], ['ranking-peg-max', '2']]) await h.input(id, value)
  await h.click('ranking-more')
  h.window.scrollTo({ top: 1840 })
  const before = h.nodes.get('ranking-list').innerHTML
  await h.stock('ranking-list', '600001')
  assert.equal(h.nodes.get('company-back')?.hidden, false)
  assert.match(h.nodes.get('company-back').textContent, /排行榜/)
  assert.match(h.location.search, /code=600001/)
  await h.click('company-back')
  assert.equal(h.nodes.get('ranking-view').hidden, false)
  assert.equal(h.nodes.get('company-view').hidden, true)
  assert.equal(h.nodes.get('ranking-list').innerHTML, before)
  assert.equal(h.nodes.get('ranking-cap-min').value, '100')
  assert.equal(h.window.scrollY, 1840)
  assert.match(h.location.search, /view=ranking/)
  h.history.forward(); await h.tick()
  assert.equal(h.nodes.get('company-view').hidden, false)
  assert.equal(h.nodes.get('stock-code').textContent, '600001.SH')
  h.history.back(); await h.tick()
  assert.equal(h.nodes.get('ranking-list').innerHTML, before)
  assert.equal(h.window.scrollY, 1840)
})

test('fund stock return preserves mode, query, loaded count and browser-forward list scroll', async () => {
  const h = await harness()
  await h.click('fund-open')
  await h.choose('fund-modes', 'fundMode', 'consensus-negative')
  await h.choose('fund-modes', 'fundMode', 'consensus-positive')
  await h.input('fund-search', '测试')
  await h.click('fund-more')
  h.window.scrollTo({ top: 1650 })
  const rows = h.nodes.get('fund-list').innerHTML
  await h.stock('fund-list', '600010')
  assert.match(h.nodes.get('company-back').textContent, /基金动向/)
  await h.click('company-back')
  assert.equal(h.nodes.get('fund-view').hidden, false)
  assert.equal(h.nodes.get('fund-search').value, '测试')
  assert.equal(h.nodes.get('fund-list').innerHTML, rows)
  assert.equal(h.window.scrollY, 1650)
  h.history.back(); await h.tick()
  assert.equal(h.nodes.get('company-view').hidden, false)
  h.history.forward(); await h.tick()
  assert.equal(h.nodes.get('fund-list').innerHTML, rows)
  assert.equal(h.window.scrollY, 1650)
})

test('Back remains available during company loading and a late success cannot overwrite restored list', async () => {
  const h = await harness()
  await h.click('ranking-open')
  h.window.scrollTo({ top: 1200 })
  const rows = h.nodes.get('ranking-list').innerHTML
  const gate = deferred()
  h.gates.set('/app/data/companies/60/600011.json', gate)
  const pending = h.stock('ranking-list', '600011')
  await h.tick()
  assert.equal(h.nodes.get('loading').hidden, false)
  assert.equal(h.nodes.get('company-back').hidden, false)
  await h.click('company-back')
  gate.resolve({ ok: true, json: async () => h.company('600011') })
  await pending
  assert.equal(h.nodes.get('ranking-view').hidden, false)
  assert.equal(h.nodes.get('company-view').hidden, true)
  assert.equal(h.nodes.get('ranking-list').innerHTML, rows)
  assert.equal(h.window.scrollY, 1200)
  assert.match(h.location.search, /view=ranking/)
})

test('company failure has Back; direct-link fallback stays in app even when initial company fails', async () => {
  const h = await harness('?code=600000', { companyError: true })
  assert.equal(h.nodes.get('error-view').hidden, false)
  assert.equal(h.nodes.get('company-back').hidden, false)
  assert.equal(h.nodes.get('ranking-open').disabled, false)
  await h.click('company-back')
  assert.equal(h.nodes.get('ranking-view').hidden, false)
  assert.match(h.location.search, /view=ranking/)
  assert.equal(h.entries.length, 1)
})

test('reloading a ranking history entry reconstructs filters, row count and scroll rather than opening default company', async () => {
  const h = await harness()
  await h.click('ranking-open')
  await h.choose('ranking-types', 'rankingKind', 'peg')
  await h.choose('ranking-years', 'rankingYears', '10')
  await h.input('ranking-search', '测试')
  await h.input('ranking-cap-min', '100')
  await h.click('ranking-more')
  h.window.scrollTo({ top: 1770 })
  // pagehide persists current controls before reload (not just on stock open).
  h.window.listeners?.pagehide?.()
  const reloaded = await harness(h.location.search, { initialEntry: { state: h.history.state, url: '/app/' + h.location.search } })
  assert.equal(reloaded.nodes.get('ranking-view').hidden, false)
  assert.equal(reloaded.nodes.get('ranking-search').value, '测试')
  assert.equal(reloaded.nodes.get('ranking-cap-min').value, '100')
  assert.equal(reloaded.nodes.get('ranking-list').innerHTML, h.nodes.get('ranking-list').innerHTML)
  assert.equal(reloaded.window.scrollY, 1770)
  assert.equal(reloaded.requests.some(url => url.includes('/companies/')), false)
})

test('returning to a fund view whose request finished while away renders data, not the saved spinner', async () => {
  const h = await harness()
  const gate = deferred()
  h.gates.set('/app/data/funds/consensus.json', gate)
  const pending = h.click('fund-open')
  await h.tick()
  await h.click('ranking-open')
  h.window.scrollTo({ top: 1300 })
  gate.resolve({ ok: true, json: async () => ({ rows: [{ code: '600001', name: '测试公司1', consensus_score: 1, direction: 'positive' }] }) })
  await pending
  assert.equal(h.nodes.get('ranking-view').hidden, false)
  assert.equal(h.window.scrollY, 1300)
  h.history.back(); await h.tick()
  assert.equal(h.nodes.get('fund-view').hidden, false)
  assert.match(h.nodes.get('fund-list').innerHTML, /data-fund-stock="600001"/)
  assert.doesNotMatch(h.nodes.get('fund-list').innerHTML, /spinner/)
})

test('search result Back restores query, result actions, company tab and scroll', async () => {
  const h = await harness()
  await h.choose('tabs', 'tab', 'cashflow')
  await h.input('stock-search', '测试公司')
  h.window.scrollTo({ top: 250 })
  await h.searchStock('600001')
  assert.match(h.nodes.get('company-back').textContent, /搜索结果/)
  await h.click('company-back')
  assert.equal(h.nodes.get('stock-search').value, '测试公司')
  assert.equal(h.nodes.get('search-results').hidden, false)
  assert.equal(h.nodes.get('stock-code').textContent, '600000.SH')
  assert.equal(h.nodes.get('panel-cashflow').classList.contains('active'), true)
  assert.equal(h.window.scrollY, 250)
  await h.searchStock('600002')
  assert.equal(h.nodes.get('stock-code').textContent, '600002.SH')
  h.history.back(); await h.tick()
  assert.equal(h.nodes.get('stock-code').textContent, '600000.SH')
})

test('reloading company with an open search restores the search instead of clearing it', async () => {
  const h = await harness()
  await h.input('stock-search', '测试公司')
  h.window.scrollTo({ top: 250 })
  h.window.listeners.pagehide()
  const reloaded = await harness(h.location.search, { initialEntry: { state: h.history.state, url: '/app/' + h.location.search } })
  assert.equal(reloaded.nodes.get('stock-search').value, '测试公司')
  assert.equal(reloaded.nodes.get('search-results').hidden, false)
  assert.equal(reloaded.window.scrollY, 250)
  await reloaded.searchStock('600003')
  assert.equal(reloaded.nodes.get('stock-code').textContent, '600003.SH')
})

test('an obsolete Forward restore cannot scroll a newer visit to the same history entry', async () => {
  const h = await harness()
  await h.click('ranking-open')
  const url = '/app/data/companies/60/600020.json'
  const original = deferred(), olderForward = deferred(), newerForward = deferred()
  h.gates.set(url, original)
  const pending = h.stock('ranking-list', '600020')
  await h.tick()
  h.history.back(); await h.tick()
  h.gates.set(url, olderForward)
  h.history.forward(); await h.tick()
  h.history.back(); await h.tick()
  h.gates.set(url, newerForward)
  h.history.forward(); await h.tick()
  newerForward.resolve({ ok: true, json: async () => h.company('600020') })
  await h.tick()
  h.window.scrollTo({ top: 990 })
  olderForward.resolve({ ok: true, json: async () => h.company('600020') })
  await h.tick()
  original.resolve({ ok: true, json: async () => h.company('600020') })
  await pending
  assert.equal(h.nodes.get('stock-code').textContent, '600020.SH')
  assert.equal(h.window.scrollY, 990)
})

export { harness, deferred }
