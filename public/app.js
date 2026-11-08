import {
  annualRows,
  classFor,
  createSingleFlightLoader,
  createViewOwner,
  deriveHighlights,
  escapeHtml,
  filterFundRows,
  filterRankingRows,
  finiteNumberText,
  formatMoney,
  formatPercent,
  fundDataUrl,
  fundEventText,
  isNavigableStockCode,
  joinRankingRows,
  rankingMetricText,
  rankingValueText,
  rankRankingRows,
  rankingUrl,
  safeFundReportUrl,
  searchStocks,
  settleOwned,
  shardUrl,
} from './lib.js'

const $ = id => document.getElementById(id)
const basePath = location.pathname.endsWith('/') ? location.pathname : location.pathname.slice(0, location.pathname.lastIndexOf('/') + 1)
const state = {
  stocks: [], activeCode: null, data: null, ready: false, companyRequest: 0,
  ranking: { kind: 'dividend', years: 3, rows: [], meta: null, visible: 100, requestId: 0, loading: false, cache: new Map() },
  funds: { mode: 'consensus-positive', consensus: [], events: [], universe: [], meta: null, visible: 100, loading: false, loaded: false },
}
const fundViewOwner = createViewOwner()

function toast(text) { const el = $('toast'); el.textContent = text; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 2200) }
function dateText(value) { return escapeHtml(value || '—') }
function latestAnnual(rows) { const annual = annualRows(rows); return annual[annual.length - 1] || rows[0] || {} }

function renderSearch(query = '') {
  const results = searchStocks(state.stocks, query, 24)
  const box = $('search-results')
  box.innerHTML = results.map(stock => `<button class="search-result" data-code="${escapeHtml(stock.code)}"><span><strong>${escapeHtml(stock.name)}</strong><small>${escapeHtml(stock.code)} · ${escapeHtml(stock.industry || stock.exchange)}</small></span><span>${escapeHtml(stock.exchange)}</span></button>`).join('') || '<div class="empty">没有匹配公司</div>'
  box.hidden = false
  box.querySelectorAll('[data-code]').forEach(button => button.onclick = () => selectCompany(button.dataset.code))
}

function metric(label, value, note = '', tone = '') { return `<article class="metric"><span>${escapeHtml(label)}</span><strong class="${escapeHtml(tone)}">${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>` }
function ratioText(numerator, denominator) {
  const top = Number(numerator), bottom = Number(denominator)
  return Number.isFinite(top) && Number.isFinite(bottom) && bottom !== 0 ? `${finiteNumberText(top / bottom, 2)}×` : '—'
}

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
  const paths = series.map(item => {
    const points = annual.map((row, i) => `${x(i)},${y(Number(row[item.key]) || 0)}`).join(' ')
    const circles = annual.map((row, i) => `<circle cx="${x(i)}" cy="${y(Number(row[item.key]) || 0)}" r="4" fill="${item.color}"/>`).join('')
    return `<polyline points="${points}" fill="none" stroke="${item.color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>${circles}`
  }).join('')
  const labels = annual.map((row, i) => `<text x="${x(i)}" y="232" text-anchor="middle" class="axis-label">${escapeHtml(String(row.period || '').slice(0, 4))}</text>`).join('')
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="营收和净利润年度趋势">${grid}${paths}${labels}</svg>`
}

function financeTable(rows) {
  const display = rows.slice(0, 16)
  if (!display.length) return '<div class="empty">暂无财务数据</div>'
  return `<div class="data-table-wrap"><table class="data-table"><thead><tr><th>报告期</th><th>营收</th><th>同比</th><th>归母净利润</th><th>同比</th><th>扣非净利润</th><th>ROE</th><th>毛利率</th><th>EPS</th></tr></thead><tbody>${display.map(row => `<tr><td>${escapeHtml(row.period_name || row.period)}</td><td>${formatMoney(row.revenue)}</td><td class="${classFor(row.revenue_yoy)}">${formatPercent(row.revenue_yoy)}</td><td>${formatMoney(row.net_profit)}</td><td class="${classFor(row.net_profit_yoy)}">${formatPercent(row.net_profit_yoy)}</td><td>${formatMoney(row.adjusted_profit)}</td><td>${formatPercent(row.roe)}</td><td>${formatPercent(row.gross_margin)}</td><td>${finiteNumberText(row.eps)}</td></tr>`).join('')}</tbody></table></div>`
}

function balancePanel(rows) {
  const latest = rows[0] || {}
  return `<div class="data-card"><h3>最新资产负债概览 · ${escapeHtml(latest.period_name || latest.period || '—')}</h3><div class="mini-grid">${metric('总资产', formatMoney(latest.assets))}${metric('总负债', formatMoney(latest.liabilities))}${metric('股东权益', formatMoney(latest.equity))}${metric('资产负债率', formatPercent(latest.debt_ratio))}${metric('流动比率', finiteNumberText(latest.current_ratio))}${metric('每股净资产', finiteNumberText(latest.bps))}</div></div><div class="data-card"><h3>历史资产负债</h3><div class="data-table-wrap"><table class="data-table"><thead><tr><th>报告期</th><th>总资产</th><th>总负债</th><th>股东权益</th><th>负债率</th><th>流动比率</th><th>速动比率</th></tr></thead><tbody>${rows.slice(0,16).map(row=>`<tr><td>${escapeHtml(row.period_name||row.period)}</td><td>${formatMoney(row.assets)}</td><td>${formatMoney(row.liabilities)}</td><td>${formatMoney(row.equity)}</td><td>${formatPercent(row.debt_ratio)}</td><td>${finiteNumberText(row.current_ratio)}</td><td>${finiteNumberText(row.quick_ratio)}</td></tr>`).join('')}</tbody></table></div></div>`
}

function cashflowPanel(rows) {
  const latest = rows[0] || {}
  return `<div class="data-card"><h3>最新现金流 · ${escapeHtml(latest.period_name || latest.period || '—')}</h3><div class="mini-grid">${metric('经营现金流', formatMoney(latest.operating_cash_flow), '', classFor(latest.operating_cash_flow))}${metric('投资现金流', formatMoney(latest.investing_cash_flow), '', classFor(latest.investing_cash_flow))}${metric('融资现金流', formatMoney(latest.financing_cash_flow), '', classFor(latest.financing_cash_flow))}${metric('每股经营现金流', finiteNumberText(latest.operating_cash_per_share))}${metric('净利现金比', ratioText(latest.operating_cash_flow, latest.net_profit))}${metric('ROIC', formatPercent(latest.roic))}</div></div><div class="data-card"><h3>历史现金流</h3><div class="data-table-wrap"><table class="data-table"><thead><tr><th>报告期</th><th>经营现金流</th><th>投资现金流</th><th>融资现金流</th><th>归母净利润</th><th>净利现金比</th></tr></thead><tbody>${rows.slice(0,16).map(row=>`<tr><td>${escapeHtml(row.period_name||row.period)}</td><td class="${classFor(row.operating_cash_flow)}">${formatMoney(row.operating_cash_flow)}</td><td class="${classFor(row.investing_cash_flow)}">${formatMoney(row.investing_cash_flow)}</td><td class="${classFor(row.financing_cash_flow)}">${formatMoney(row.financing_cash_flow)}</td><td>${formatMoney(row.net_profit)}</td><td>${ratioText(row.operating_cash_flow, row.net_profit)}</td></tr>`).join('')}</tbody></table></div></div>`
}

function dividendPanel(rows, price) {
  if (!rows.length) return '<div class="data-card empty">暂无分红记录</div>'
  return `<div class="data-card"><h3>历年分红方案</h3><div class="dividend-list">${rows.map(row => {
    const cash = Number(row.cash_per_10), currentPrice = Number(price)
    const yieldText = Number.isFinite(cash) && Number.isFinite(currentPrice) && currentPrice !== 0 ? ` · ${finiteNumberText(cash / 10 / currentPrice * 100, 2)}%` : ''
    const cashText = Number.isFinite(cash) ? `10派${finiteNumberText(cash)}元` : '—'
    const year = escapeHtml(String(row.period || '').slice(0, 4) || '—')
    return `<article class="dividend-item"><time>${year}年度</time><div><strong>${escapeHtml(row.plan || '未披露方案')}</strong><p>${escapeHtml(row.progress || '—')} · 除权日 ${dateText(row.ex_date)}</p></div><b>${cashText}${yieldText}</b></article>`
  }).join('')}</div></div>`
}

function rankingDescription() {
  if (state.ranking.kind === 'dividend') {
    return `过去${state.ranking.years}个完整财年的平均现金分红 ÷ 当前总市值；未分红年度按0计。`
  }
  if (state.ranking.kind === 'peg') {
    const growth = state.ranking.years === 1 ? '最近两个完整财年的归母净利润增长率' : `过去${state.ranking.years}个完整财年的归母净利润CAGR`
    return `PEG按从低到高排序：当前动态PE ÷ ${growth}；不可计算项列于有效排名之后。`
  }
  return '综合得分：营收CAGR 30% + 归母净利润CAGR 30% + 平均ROE 30% + 盈利年份占比 10%。'
}

function rankingFilterNumber(id, scale = 1) {
  const raw = $(id).value.trim()
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value * scale : null
}

function rankingFilterOptions() {
  const pegRanking = state.ranking.kind === 'peg'
  return {
    minMarketCap: rankingFilterNumber('ranking-cap-min', 1e8),
    maxMarketCap: rankingFilterNumber('ranking-cap-max', 1e8),
    minPeg: pegRanking ? rankingFilterNumber('ranking-peg-min') : null,
    maxPeg: pegRanking ? rankingFilterNumber('ranking-peg-max') : null,
  }
}

function rankingRow(row) {
  const { value, label } = rankingValueText(state.ranking.kind, row)
  const details = rankingMetricText(state.ranking.kind, row, state.ranking.years)
  return `<button class="ranking-row" data-ranking-code="${escapeHtml(row.code)}" type="button"><span class="ranking-position">${finiteNumberText(row.rank)}</span><span class="ranking-company"><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.code)} · ${escapeHtml(row.industry || row.exchange)}</small><em>${escapeHtml(details)}</em></span><span class="ranking-value"><strong>${escapeHtml(value)}</strong><small>${escapeHtml(label)}</small></span></button>`
}

function renderRanking() {
  if (state.ranking.loading) return
  $('ranking-description').textContent = rankingDescription()
  const filtered = filterRankingRows(state.ranking.rows, $('ranking-search').value, rankingFilterOptions())
  const visible = filtered.slice(0, state.ranking.visible)
  const endYear = state.ranking.meta?.end_year || '—'
  if (state.ranking.kind === 'peg') {
    const valid = filtered.filter(row => row.peg != null).length
    $('ranking-status').textContent = `统计截至${endYear}财年 · ${valid.toLocaleString()}家可计算PEG · ${(filtered.length - valid).toLocaleString()}家不可计算（列于后部）`
  } else {
    $('ranking-status').textContent = `统计截至${endYear}财年 · ${filtered.length.toLocaleString()}家公司符合完整数据要求`
  }
  $('ranking-list').innerHTML = visible.map(rankingRow).join('') || '<div class="data-card empty">没有符合条件的公司</div>'
  $('ranking-more').hidden = visible.length >= filtered.length
}

function syncRankingControls() {
  document.querySelectorAll('[data-ranking-kind]').forEach(button => button.classList.toggle('active', button.dataset.rankingKind === state.ranking.kind))
  document.querySelectorAll('[data-ranking-years]').forEach(button => button.classList.toggle('active', Number(button.dataset.rankingYears) === state.ranking.years))
  $('ranking-peg-range').hidden = state.ranking.kind !== 'peg'
  $('ranking-filter-note').textContent = state.ranking.kind === 'peg'
    ? 'PEG区间使用当前所选1/3/5/10年增长口径；PE、增长率、公式和利润起止值显示在每行。'
    : '可按当前总市值筛选；切换到PEG排行可查看对应周期的完整计算明细。'
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
  fundViewOwner.invalidate()
  $('loading').hidden = true
  $('error-view').hidden = true
  $('company-view').hidden = true
  $('fund-view').hidden = true
  $('ranking-view').hidden = false
  loadRanking()
  window.scrollTo({ top: Math.max(0, $('ranking-view').offsetTop - 78), behavior: 'smooth' })
}

const shareNumber = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })

function fundConsensusRow(row, index) {
  const managers = [...new Set((row.events || []).map(event => event.manager_name).filter(Boolean))].slice(0, 3).join('、') || '经理未知'
  const rawScore = Number(row.consensus_score)
  const score = Number.isFinite(rawScore) ? rawScore : 0
  const scoreText = `${score > 0 ? '+' : ''}${finiteNumberText(score, 1, '0.0')}`
  return `<button class="fund-consensus-row" data-fund-stock="${escapeHtml(row.code)}" type="button"><span class="ranking-position">${finiteNumberText(index + 1, 0)}</span><span class="ranking-company"><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.code)} · ${escapeHtml(row.industry || '未分类')}</small><em>${finiteNumberText(row.independent_manager_count, 0)}位独立经理 · ${finiteNumberText(row.fund_count, 0)}只基金 · ${finiteNumberText(row.confirmed_event_count, 0)}项确认事件 · ${escapeHtml(managers)}</em></span><span class="ranking-value"><strong class="${score >= 0 ? 'positive' : 'negative'}">${scoreText}</strong><small>共识分</small></span></button>`
}

function fundEventRow(event) {
  const sharesChange = Number(event.shares_change)
  const change = event.shares_change == null || !Number.isFinite(sharesChange)
    ? '—'
    : `${sharesChange > 0 ? '+' : ''}${shareNumber.format(sharesChange)}股`
  const reportUrl = safeFundReportUrl(event.source_url)
  const source = reportUrl ? `<a href="${escapeHtml(reportUrl)}" target="_blank" rel="noopener">原始报告 ↗</a>` : ''
  const navigable = isNavigableStockCode(event.stock_code, state.stocks)
  const content = `<span><strong>${escapeHtml(event.stock_name)}</strong><small>${escapeHtml(event.stock_code)} · ${escapeHtml(event.fund_name)} · ${escapeHtml(event.manager_name || '经理未知')}</small><em>${escapeHtml(fundEventText(event))} · 持股变化 ${change}</em></span><b>${escapeHtml(event.report_period || '—')}</b>`
  const body = navigable
    ? `<button class="fund-event-content" data-fund-stock="${escapeHtml(event.stock_code)}" type="button">${content}</button>`
    : `<div class="fund-event-content" aria-disabled="true">${content}</div>`
  return `<article class="fund-event-row">${body}${source}</article>`
}

function fundUniverseRow(fund, index) {
  const rawScore = Number(fund.screen_score)
  const score = Number.isFinite(rawScore) ? rawScore : 0
  const size = fund.fund_size == null ? '—' : `${finiteNumberText(fund.fund_size, 2)}亿`
  return `<article class="fund-universe-row"><span class="ranking-position">${finiteNumberText(index + 1, 0)}</span><span><strong>${escapeHtml(fund.name)}</strong><small>${escapeHtml(fund.code)} · ${escapeHtml(fund.manager_name || '经理未知')} · 规模 ${size}</small><em>近3年 ${formatPercent(fund.return_3y)} · 抗风险 ${finiteNumberText(fund.risk_score)} · 稳定性 ${finiteNumberText(fund.stability_score)}</em></span><b>${finiteNumberText(score, 1, '0.0')}<small>筛选分</small></b></article>`
}

function syncFundControls() {
  document.querySelectorAll('[data-fund-mode]').forEach(button => button.classList.toggle('active', button.dataset.fundMode === state.funds.mode))
}

function renderFundView() {
  syncFundControls()
  const source = state.funds.mode === 'universe'
    ? state.funds.universe
    : state.funds.mode.startsWith('consensus-') ? state.funds.consensus : state.funds.events
  const filtered = filterFundRows(source, $('fund-search').value, state.funds.mode)
  const visible = filtered.slice(0, state.funds.visible)
  const period = state.funds.meta?.report_period || state.funds.consensus[0]?.report_period || '—'
  $('fund-status').textContent = `报告期 ${period} · ${filtered.length}项 · 研究池 ${state.funds.universe.length}只`
  if (state.funds.mode === 'universe') {
    $('fund-list').innerHTML = visible.map(fundUniverseRow).join('') || '<div class="data-card empty">没有匹配基金</div>'
  } else if (state.funds.mode.startsWith('consensus-')) {
    $('fund-list').innerHTML = visible.map(fundConsensusRow).join('') || '<div class="data-card empty">当前研究池没有匹配的共识信号</div>'
  } else {
    $('fund-list').innerHTML = visible.map(fundEventRow).join('') || '<div class="data-card empty">当前研究池没有匹配的持仓事件</div>'
  }
  $('fund-more').hidden = visible.length >= filtered.length
}

const fundDataLoader = createSingleFlightLoader(async () => {
  state.funds.loading = true
  const names = ['consensus', 'events', 'universe']
  try {
    const responses = await Promise.all(names.map(name => fetch(fundDataUrl(basePath, name))))
    responses.forEach((response, index) => { if (!response.ok) throw new Error(`基金${names[index]}数据返回 ${response.status}`) })
    const [consensus, events, universe] = await Promise.all(responses.map(response => response.json()))
    state.funds.consensus = consensus.rows || []
    state.funds.events = events.events || []
    state.funds.universe = universe.funds || []
    state.funds.meta = consensus.meta || events.meta || universe.meta || null
    state.funds.loaded = true
    return state.funds
  } finally {
    state.funds.loading = false
  }
})

const loadFundData = () => fundDataLoader.load()

async function openFundView() {
  if (!state.ready) {
    toast('市场目录仍在载入，请稍候')
    return
  }
  const viewToken = fundViewOwner.claim('fund-view')
  state.companyRequest += 1
  state.ranking.requestId += 1
  state.ranking.loading = false
  $('loading').hidden = true
  $('error-view').hidden = true
  $('company-view').hidden = true
  $('ranking-view').hidden = true
  $('fund-view').hidden = false
  $('fund-list').innerHTML = '<div class="loading-card compact"><div class="spinner"></div><strong>基金报告加载中</strong></div>'
  $('fund-status').textContent = '正在载入基金动向…'
  await settleOwned(fundViewOwner, viewToken, loadFundData(), {
    success: () => {
      state.funds.visible = 100
      renderFundView()
    },
    failure: error => {
      $('fund-status').textContent = '基金动向载入失败'
      $('fund-list').innerHTML = `<div class="error-card"><strong>暂时无法读取基金数据</strong><p>${escapeHtml(error.message)}</p></div>`
    },
    settled: () => window.scrollTo({ top: Math.max(0, $('fund-view').offsetTop - 78), behavior: 'smooth' }),
  })
}

async function renderCompanyFundPanel(code) {
  const viewToken = fundViewOwner.claim(`company:${code}`)
  const panel = $('panel-funds')
  panel.innerHTML = '<div class="loading-card compact"><div class="spinner"></div><strong>基金动向加载中</strong></div>'
  await settleOwned(fundViewOwner, viewToken, loadFundData(), {
    success: () => {
      if (state.activeCode !== code) return
      const consensus = state.funds.consensus.find(row => row.code === code)
      const events = state.funds.events.filter(event => event.stock_code === code && !['CENSORED', 'UNCHANGED'].includes(event.event_type))
      if (!consensus && !events.length) {
        panel.innerHTML = '<div class="data-card empty">当前研究池未披露该公司的明确持仓动作。未出现不代表基金没有持有。</div>'
        return
      }
      const summary = consensus ? `<div class="mini-grid">${metric('基金共识分', finiteNumberText(consensus.consensus_score, 1))}${metric('独立经理', finiteNumberText(consensus.independent_manager_count, 0))}${metric('涉及基金', finiteNumberText(consensus.fund_count, 0))}${metric('确认事件', finiteNumberText(consensus.confirmed_event_count, 0))}</div>` : ''
      panel.innerHTML = `<div class="data-card"><h3>筛选基金研究池动向</h3>${summary}<div class="company-fund-events">${events.map(fundEventRow).join('') || '<div class="empty">只有聚合共识，暂无可单列事件</div>'}</div><p class="fund-disclosure-note">基金季报通常只披露前十大；中报和年报完整持仓才可确认新建或清仓。信号从公告后才对投资者可见。</p></div>`
    },
    failure: error => {
      panel.innerHTML = `<div class="error-card"><strong>基金动向读取失败</strong><p>${escapeHtml(error.message)}</p></div>`
    },
  })
}

async function selectCompany(code) {
  const requestId = ++state.companyRequest
  const fundTabActive = document.querySelector('[data-tab="funds"]').classList.contains('active')
  fundViewOwner.invalidate()
  $('panel-funds').innerHTML = ''
  state.ranking.requestId += 1
  state.ranking.loading = false
  $('fund-view').hidden = true
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
    $('stock-listing').textContent = stock.listing_date ? '上市 ' + String(stock.listing_date) : ''
    $('latest-period').textContent = latest.period_name || latest.period || '—'
    $('latest-notice').textContent = latest.notice_date ? '披露 ' + String(latest.notice_date) : '披露日期未知'
    const annual = latestAnnual(data.financials)
    const annualYear = String(annual.period || '').slice(0, 4) || '—'
    $('highlights').innerHTML = metric('营业收入', formatMoney(highlights.revenue), formatPercent(latest.revenue_yoy), classFor(latest.revenue_yoy)) + metric('归母净利润', formatMoney(highlights.netProfit), formatPercent(latest.net_profit_yoy), classFor(latest.net_profit_yoy)) + metric('加权ROE', formatPercent(highlights.roe), '年报 ' + annualYear) + metric('最近现金分红', highlights.lastCashPer10 == null ? '—' : `${finiteNumberText(highlights.lastCashPer10, 2)}元`, '每10股（已实施）')
    $('profit-chart').innerHTML = lineChart(data.financials)
    $('panel-overview').innerHTML = `<div class="data-card"><h3>主要财务指标</h3>${financeTable(data.financials)}</div>`
    $('panel-balance').innerHTML = balancePanel(data.financials)
    $('panel-cashflow').innerHTML = cashflowPanel(data.financials)
    $('panel-dividend').innerHTML = dividendPanel(data.dividends, stock.price)
    history.replaceState({}, '', `${basePath}?code=${code}`)
    $('loading').hidden = true; $('company-view').hidden = false
    if (fundTabActive) renderCompanyFundPanel(code)
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
    $('fund-open').disabled = false
  } catch (error) { $('loading').hidden = true; $('error-view').hidden = false; $('error-message').textContent = error.message }
}

$('stock-search').addEventListener('input', event => renderSearch(event.target.value))
$('stock-search').addEventListener('focus', event => renderSearch(event.target.value))
document.addEventListener('click', event => { if (!event.target.closest('.search-wrap')) $('search-results').hidden = true })
$('tabs').addEventListener('click', event => { const button=event.target.closest('[data-tab]'); if(!button)return; document.querySelectorAll('#tabs button').forEach(x=>x.classList.toggle('active',x===button)); document.querySelectorAll('.tab-panel').forEach(x=>x.classList.toggle('active',x.id===`panel-${button.dataset.tab}`)); if(button.dataset.tab==='funds'&&state.activeCode)renderCompanyFundPanel(state.activeCode) })
$('ranking-open').addEventListener('click', openRanking)
$('fund-open').addEventListener('click', openFundView)
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
$('fund-close').addEventListener('click', () => {
  fundViewOwner.invalidate()
  $('fund-view').hidden = true
  if (state.data) $('company-view').hidden = false
})
$('fund-modes').addEventListener('click', event => { const button=event.target.closest('[data-fund-mode]'); if(!button)return; state.funds.mode=button.dataset.fundMode; state.funds.visible=100; renderFundView() })
$('fund-search').addEventListener('input', () => { state.funds.visible=100; renderFundView() })
$('fund-list').addEventListener('click', event => { const target=event.target.closest('[data-fund-stock]'); if(target)selectCompany(target.dataset.fundStock) })
$('fund-more').addEventListener('click', () => { state.funds.visible += 100; renderFundView() })
if ('serviceWorker' in navigator) window.addEventListener('load',()=>navigator.serviceWorker.register(`${basePath}sw.js`).catch(()=>{}))
init()
