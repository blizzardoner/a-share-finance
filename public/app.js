import {
  annualRows,
  classFor,
  deriveHighlights,
  filterRankingRows,
  formatMoney,
  formatPercent,
  joinRankingRows,
  rankingMetricText,
  rankRankingRows,
  rankingUrl,
  searchStocks,
  shardUrl,
} from './lib.js'

const $ = id => document.getElementById(id)
const basePath = location.pathname.endsWith('/') ? location.pathname : location.pathname.slice(0, location.pathname.lastIndexOf('/') + 1)
const state = {
  stocks: [], activeCode: null, data: null, ready: false, companyRequest: 0,
  ranking: { kind: 'dividend', years: 3, rows: [], meta: null, visible: 100, requestId: 0, loading: false, cache: new Map() },
}

function toast(text) { const el = $('toast'); el.textContent = text; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 2200) }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[c]) }
function dateText(value) { return value || '—' }
function latestAnnual(rows) { const annual = annualRows(rows); return annual[annual.length - 1] || rows[0] || {} }

function renderSearch(query = '') {
  const results = searchStocks(state.stocks, query, 24)
  const box = $('search-results')
  box.innerHTML = results.map(stock => `<button class="search-result" data-code="${stock.code}"><span><strong>${escapeHtml(stock.name)}</strong><small>${stock.code} · ${escapeHtml(stock.industry || stock.exchange)}</small></span><span>${stock.exchange}</span></button>`).join('') || '<div class="empty">没有匹配公司</div>'
  box.hidden = false
  box.querySelectorAll('[data-code]').forEach(button => button.onclick = () => selectCompany(button.dataset.code))
}

function metric(label, value, note = '', tone = '') { return `<article class="metric"><span>${label}</span><strong class="${tone}">${value}</strong><small>${note}</small></article>` }

function lineChart(rows) {
  const annual = annualRows(rows).slice(-6)
  if (annual.length < 2) return '<div class="empty">年度数据不足</div>'
  const series = [
    { key: 'revenue', color: '#8b72ff' },
    { key: 'net_profit', color: '#4ed7dd' },
  ]
  const all = annual.flatMap(row => series.map(item => Number(row[item.key]) || 0))
  const max = Math.max(...all, 1), min = Math.min(...all, 0), span = max - min || 1
  const w = 720, h = 240, px = 44, py = 22, chartH = h - py * 2
  const x = i => px + i / Math.max(1, annual.length - 1) * (w - px * 2)
  const y = value => py + (max - value) / span * chartH
  const grid = [0, .25, .5, .75, 1].map(v => `<line x1="${px}" y1="${py + v * chartH}" x2="${w - px}" y2="${py + v * chartH}" stroke="rgba(149,168,205,.11)"/>`).join('')
  const paths = series.map(item => { const points = annual.map((row, i) => `${x(i)},${y(Number(row[item.key]) || 0)}`).join(' '); return `<polyline points="${points}" fill="none" stroke="${item.color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>${annual.map((row, i) => `<circle cx="${x(i)}" cy="${y(Number(row[item.key]) || 0)}" r="4" fill="${item.color}"/>`).join('')}` }).join('')
  const labels = annual.map((row, i) => `<text x="${x(i)}" y="232" text-anchor="middle" class="axis-label">${row.period.slice(0, 4)}</text>`).join('')
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="营收和净利润年度趋势">${grid}${paths}${labels}</svg>`
}

function financeTable(rows) {
  const display = rows.slice(0, 16)
  if (!display.length) return '<div class="empty">暂无财务数据</div>'
  return `<div class="data-table-wrap"><table class="data-table"><thead><tr><th>报告期</th><th>营收</th><th>同比</th><th>归母净利润</th><th>同比</th><th>扣非净利润</th><th>ROE</th><th>毛利率</th><th>EPS</th></tr></thead><tbody>${display.map(row => `<tr><td>${escapeHtml(row.period_name || row.period)}</td><td>${formatMoney(row.revenue)}</td><td class="${classFor(row.revenue_yoy)}">${formatPercent(row.revenue_yoy)}</td><td>${formatMoney(row.net_profit)}</td><td class="${classFor(row.net_profit_yoy)}">${formatPercent(row.net_profit_yoy)}</td><td>${formatMoney(row.adjusted_profit)}</td><td>${formatPercent(row.roe)}</td><td>${formatPercent(row.gross_margin)}</td><td>${row.eps ?? '—'}</td></tr>`).join('')}</tbody></table></div>`
}

function balancePanel(rows) {
  const latest = rows[0] || {}
  return `<div class="data-card"><h3>最新资产负债概览 · ${escapeHtml(latest.period_name || latest.period || '—')}</h3><div class="mini-grid">${metric('总资产', formatMoney(latest.assets))}${metric('总负债', formatMoney(latest.liabilities))}${metric('股东权益', formatMoney(latest.equity))}${metric('资产负债率', formatPercent(latest.debt_ratio))}${metric('流动比率', latest.current_ratio ?? '—')}${metric('每股净资产', latest.bps ?? '—')}</div></div><div class="data-card"><h3>历史资产负债</h3><div class="data-table-wrap"><table class="data-table"><thead><tr><th>报告期</th><th>总资产</th><th>总负债</th><th>股东权益</th><th>负债率</th><th>流动比率</th><th>速动比率</th></tr></thead><tbody>${rows.slice(0,16).map(row=>`<tr><td>${escapeHtml(row.period_name||row.period)}</td><td>${formatMoney(row.assets)}</td><td>${formatMoney(row.liabilities)}</td><td>${formatMoney(row.equity)}</td><td>${formatPercent(row.debt_ratio)}</td><td>${row.current_ratio??'—'}</td><td>${row.quick_ratio??'—'}</td></tr>`).join('')}</tbody></table></div></div>`
}

function cashflowPanel(rows) {
  const latest = rows[0] || {}
  return `<div class="data-card"><h3>最新现金流 · ${escapeHtml(latest.period_name || latest.period || '—')}</h3><div class="mini-grid">${metric('经营现金流', formatMoney(latest.operating_cash_flow), '', classFor(latest.operating_cash_flow))}${metric('投资现金流', formatMoney(latest.investing_cash_flow), '', classFor(latest.investing_cash_flow))}${metric('融资现金流', formatMoney(latest.financing_cash_flow), '', classFor(latest.financing_cash_flow))}${metric('每股经营现金流', latest.operating_cash_per_share ?? '—')}${metric('净利现金比', latest.net_profit && latest.operating_cash_flow ? `${(latest.operating_cash_flow/latest.net_profit).toFixed(2)}×` : '—')}${metric('ROIC', formatPercent(latest.roic))}</div></div><div class="data-card"><h3>历史现金流</h3><div class="data-table-wrap"><table class="data-table"><thead><tr><th>报告期</th><th>经营现金流</th><th>投资现金流</th><th>融资现金流</th><th>归母净利润</th><th>净利现金比</th></tr></thead><tbody>${rows.slice(0,16).map(row=>`<tr><td>${escapeHtml(row.period_name||row.period)}</td><td class="${classFor(row.operating_cash_flow)}">${formatMoney(row.operating_cash_flow)}</td><td class="${classFor(row.investing_cash_flow)}">${formatMoney(row.investing_cash_flow)}</td><td class="${classFor(row.financing_cash_flow)}">${formatMoney(row.financing_cash_flow)}</td><td>${formatMoney(row.net_profit)}</td><td>${row.net_profit&&row.operating_cash_flow?(row.operating_cash_flow/row.net_profit).toFixed(2)+'×':'—'}</td></tr>`).join('')}</tbody></table></div></div>`
}

function dividendPanel(rows, price) {
  if (!rows.length) return '<div class="data-card empty">暂无分红记录</div>'
  return `<div class="data-card"><h3>历年分红方案</h3><div class="dividend-list">${rows.map(row=>{const y=price&&row.cash_per_10?(row.cash_per_10/10/price*100):null;return `<article class="dividend-item"><time>${row.period?.slice(0,4)||'—'}年度</time><div><strong>${escapeHtml(row.plan||'未披露方案')}</strong><p>${escapeHtml(row.progress||'—')} · 除权日 ${dateText(row.ex_date)}</p></div><b>${row.cash_per_10!=null?`10派${row.cash_per_10}元`:'—'}${y!=null?` · ${y.toFixed(2)}%`:''}</b></article>`}).join('')}</div></div>`
}

function rankingDescription() {
  if (state.ranking.kind === 'dividend') {
    return `过去${state.ranking.years}个完整财年的平均现金分红 ÷ 当前总市值；未分红年度按0计。`
  }
  return '综合得分：营收CAGR 30% + 归母净利润CAGR 30% + 平均ROE 30% + 盈利年份占比 10%。'
}

function rankingPercent(value, signed = false) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function rankingFilterNumber(id, scale = 1) {
  const raw = $(id).value.trim()
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value * scale : null
}

function rankingFilterOptions() {
  return {
    minMarketCap: rankingFilterNumber('ranking-cap-min', 1e8),
    maxMarketCap: rankingFilterNumber('ranking-cap-max', 1e8),
    minPeg: rankingFilterNumber('ranking-peg-min'),
    maxPeg: rankingFilterNumber('ranking-peg-max'),
  }
}

function rankingRow(row) {
  const dividend = state.ranking.kind === 'dividend'
  const value = dividend ? rankingPercent(row.yield) : Number(row.score).toFixed(1)
  const label = dividend ? '平均分红率' : '综合得分'
  const details = rankingMetricText(state.ranking.kind, row, state.ranking.years)
  return `<button class="ranking-row" data-ranking-code="${row.code}" type="button"><span class="ranking-position">${row.rank}</span><span class="ranking-company"><strong>${escapeHtml(row.name)}</strong><small>${row.code} · ${escapeHtml(row.industry || row.exchange)}</small><em>${details}</em></span><span class="ranking-value"><strong>${value}</strong><small>${label}</small></span></button>`
}

function renderRanking() {
  if (state.ranking.loading) return
  $('ranking-description').textContent = rankingDescription()
  const filtered = filterRankingRows(state.ranking.rows, $('ranking-search').value, rankingFilterOptions())
  const visible = filtered.slice(0, state.ranking.visible)
  const endYear = state.ranking.meta?.end_year || '—'
  $('ranking-status').textContent = `统计截至${endYear}财年 · ${filtered.length.toLocaleString()}家公司符合完整数据要求`
  $('ranking-list').innerHTML = visible.map(rankingRow).join('') || '<div class="data-card empty">没有符合条件的公司</div>'
  $('ranking-more').hidden = visible.length >= filtered.length
}

function syncRankingControls() {
  document.querySelectorAll('[data-ranking-kind]').forEach(button => button.classList.toggle('active', button.dataset.rankingKind === state.ranking.kind))
  document.querySelectorAll('[data-ranking-years]').forEach(button => button.classList.toggle('active', Number(button.dataset.rankingYears) === state.ranking.years))
}

async function loadRanking() {
  if (!state.ready) return
  syncRankingControls()
  const kind = state.ranking.kind
  const key = `${kind}-${state.ranking.years}`
  const requestId = ++state.ranking.requestId
  state.ranking.visible = 100
  state.ranking.rows = []
  state.ranking.meta = null
  state.ranking.loading = true
  $('ranking-status').textContent = '正在载入排名…'
  $('ranking-list').innerHTML = '<div class="loading-card compact"><div class="spinner"></div><strong>计算结果加载中</strong></div>'
  $('ranking-more').hidden = true
  try {
    let payload = state.ranking.cache.get(key)
    if (!payload) {
      const response = await fetch(rankingUrl(basePath, kind, state.ranking.years))
      if (!response.ok) throw new Error(`排名数据返回 ${response.status}`)
      payload = await response.json()
      if (state.ranking.requestId !== requestId) return
      state.ranking.cache.set(key, payload)
    }
    if (state.ranking.requestId !== requestId) return
    const ranked = rankRankingRows(payload.rows || [], kind)
    state.ranking.rows = joinRankingRows(ranked, state.stocks)
    state.ranking.meta = payload.meta || null
    state.ranking.loading = false
    renderRanking()
  } catch (error) {
    if (state.ranking.requestId !== requestId) return
    state.ranking.loading = false
    $('ranking-status').textContent = '排名载入失败'
    $('ranking-list').innerHTML = `<div class="error-card"><strong>暂时无法读取排名</strong><p>${escapeHtml(error.message)}</p></div>`
  }
}

function openRanking() {
  if (!state.ready) {
    toast('市场目录仍在载入，请稍候')
    return
  }
  state.companyRequest += 1
  $('loading').hidden = true
  $('error-view').hidden = true
  $('company-view').hidden = true
  $('ranking-view').hidden = false
  loadRanking()
  window.scrollTo({ top: Math.max(0, $('ranking-view').offsetTop - 78), behavior: 'smooth' })
}

async function selectCompany(code) {
  const requestId = ++state.companyRequest
  state.ranking.requestId += 1
  state.ranking.loading = false
  $('ranking-view').hidden = true
  $('search-results').hidden = true
  $('stock-search').value = ''
  $('company-view').hidden = true
  $('loading').hidden = false
  try {
    const response = await fetch(shardUrl(basePath, code))
    if (!response.ok) throw new Error(`公司数据返回 ${response.status}`)
    const data = await response.json()
    if (state.companyRequest !== requestId) return false
    state.activeCode = code; state.data = data
    const stock = data.stock, latest = data.financials[0] || {}, highlights = deriveHighlights(data)
    $('stock-exchange').textContent = stock.exchange
    $('stock-industry').textContent = stock.industry || '未分类'
    $('stock-name').textContent = stock.name
    $('stock-code').textContent = stock.secucode
    $('stock-listing').textContent = stock.listing_date ? `上市 ${stock.listing_date}` : ''
    $('latest-period').textContent = latest.period_name || latest.period || '—'
    $('latest-notice').textContent = latest.notice_date ? `披露 ${latest.notice_date}` : '披露日期未知'
    const annual = latestAnnual(data.financials)
    $('highlights').innerHTML = metric('营业收入', formatMoney(highlights.revenue), formatPercent(latest.revenue_yoy), classFor(latest.revenue_yoy)) + metric('归母净利润', formatMoney(highlights.netProfit), formatPercent(latest.net_profit_yoy), classFor(latest.net_profit_yoy)) + metric('加权ROE', formatPercent(highlights.roe), `年报 ${annual.period?.slice(0,4)||'—'}`) + metric('最近现金分红', highlights.lastCashPer10 == null ? '—' : `${Number(highlights.lastCashPer10).toFixed(2)}元`, '每10股（已实施）')
    $('profit-chart').innerHTML = lineChart(data.financials)
    $('panel-overview').innerHTML = `<div class="data-card"><h3>主要财务指标</h3>${financeTable(data.financials)}</div>`
    $('panel-balance').innerHTML = balancePanel(data.financials)
    $('panel-cashflow').innerHTML = cashflowPanel(data.financials)
    $('panel-dividend').innerHTML = dividendPanel(data.dividends, stock.price)
    history.replaceState({}, '', `${basePath}?code=${code}`)
    $('loading').hidden = true; $('company-view').hidden = false
    window.scrollTo({ top: Math.max(0, document.querySelector('.company-head').offsetTop - 90), behavior: 'smooth' })
    return true
  } catch (error) {
    if (state.companyRequest !== requestId) return false
    $('loading').hidden = true; $('error-view').hidden = false; $('error-message').textContent = error.message
    return false
  }
}

async function init() {
  try {
    const response = await fetch(`${basePath}data/stocks.json`)
    if (!response.ok) throw new Error(`市场目录返回 ${response.status}`)
    const payload = await response.json()
    state.stocks = payload.stocks || []
    $('data-count').textContent = `${state.stocks.length.toLocaleString()} 家公司`
    $('generated-at').textContent = `更新于 ${payload.meta?.generated_at?.replace('T',' ').replace('Z',' UTC') || '—'}`
    const requested = new URLSearchParams(location.search).get('code')
    const preferred = requested && state.stocks.some(x=>x.code===requested) ? requested : (state.stocks.find(x=>x.code==='600519')?.code || state.stocks[0]?.code)
    if (!preferred) throw new Error('市场目录为空')
    if (!await selectCompany(preferred)) return
    state.ready = true
    $('stock-search').disabled = false
    $('ranking-open').disabled = false
  } catch (error) { $('loading').hidden = true; $('error-view').hidden = false; $('error-message').textContent = error.message }
}

$('stock-search').addEventListener('input', event => renderSearch(event.target.value))
$('stock-search').addEventListener('focus', event => renderSearch(event.target.value))
document.addEventListener('click', event => { if (!event.target.closest('.search-wrap')) $('search-results').hidden = true })
$('tabs').addEventListener('click', event => { const button=event.target.closest('[data-tab]'); if(!button)return; document.querySelectorAll('#tabs button').forEach(x=>x.classList.toggle('active',x===button)); document.querySelectorAll('.tab-panel').forEach(x=>x.classList.toggle('active',x.id===`panel-${button.dataset.tab}`)) })
$('ranking-open').addEventListener('click', openRanking)
$('ranking-close').addEventListener('click', () => {
  state.ranking.requestId += 1
  state.ranking.loading = false
  $('ranking-view').hidden = true
  if (state.data) $('company-view').hidden = false
})
$('ranking-types').addEventListener('click', event => { const button = event.target.closest('[data-ranking-kind]'); if (!button) return; state.ranking.kind = button.dataset.rankingKind; loadRanking() })
$('ranking-years').addEventListener('click', event => { const button = event.target.closest('[data-ranking-years]'); if (!button) return; state.ranking.years = Number(button.dataset.rankingYears); loadRanking() })
const rankingFilterIds = ['ranking-search', 'ranking-cap-min', 'ranking-cap-max', 'ranking-peg-min', 'ranking-peg-max']
rankingFilterIds.forEach(id => $(id).addEventListener('input', () => { state.ranking.visible = 100; renderRanking() }))
$('ranking-filter-reset').addEventListener('click', () => {
  rankingFilterIds.forEach(id => { $(id).value = '' })
  state.ranking.visible = 100
  renderRanking()
})
$('ranking-list').addEventListener('click', event => { const button = event.target.closest('[data-ranking-code]'); if (button) selectCompany(button.dataset.rankingCode) })
$('ranking-more').addEventListener('click', () => { state.ranking.visible += 100; renderRanking() })
if ('serviceWorker' in navigator) window.addEventListener('load',()=>navigator.serviceWorker.register(`${basePath}sw.js`).catch(()=>{}))
init()
