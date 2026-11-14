const compact = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character])
}

export function finiteNumberText(value, digits = null, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback
  const number = Number(value)
  if (!Number.isFinite(number)) return fallback
  return digits === null ? String(number) : number.toFixed(digits)
}

export function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  const abs = Math.abs(n)
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return compact.format(n)
}

export function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

export function annualRows(rows = []) {
  return rows
    .filter(row => row.report_type === '年报' || row.period?.endsWith('-12-31'))
    .sort((a, b) => a.period.localeCompare(b.period))
}

export function deriveHighlights(data) {
  const latest = data.financials?.[0] || {}
  const implemented = (data.dividends || []).find(item => item.progress?.includes('实施')) || {}
  return {
    revenue: latest.revenue ?? null,
    netProfit: latest.net_profit ?? null,
    roe: latest.roe ?? null,
    lastCashPer10: implemented.cash_per_10 ?? null,
  }
}

export function shardUrl(basePath, code) {
  const base = basePath.endsWith('/') ? basePath : `${basePath}/`
  return `${base}data/companies/${code.slice(0, 2)}/${code}.json`
}

export function rankingUrl(basePath, kind, years) {
  const base = basePath.endsWith('/') ? basePath : `${basePath}/`
  return `${base}data/rankings/${kind}-${years}.json`
}

export function fundDataUrl(basePath, name) {
  const base = basePath.endsWith('/') ? basePath : `${basePath}/`
  return `${base}data/funds/${name}.json`
}

export function fundStockEventsUrl(basePath, code) {
  const base = basePath.endsWith('/') ? basePath : `${basePath}/`
  return `${base}data/funds/stocks/${code.slice(0, 2)}/${code}.json`
}

export function fundTrackingStatusText(status) {
  if (status === 'tracked') return { label: '持仓已更新', short: '已更新' }
  if (status === 'update_failed') return { label: '更新失败，沿用旧数据', short: '更新失败' }
  return { label: '等待轮询', short: '待更新' }
}

export function createSingleFlightLoader(loadValue) {
  let loaded = false
  let value
  let inFlight = null
  return {
    load() {
      if (loaded) return Promise.resolve(value)
      if (inFlight) return inFlight
      try {
        inFlight = Promise.resolve(loadValue())
      } catch (error) {
        inFlight = Promise.reject(error)
      }
      inFlight = inFlight
        .then(result => { loaded = true; value = result; return result })
        .finally(() => { inFlight = null })
      return inFlight
    },
  }
}

export function createViewOwner() {
  let generation = 0
  let owner = null
  return {
    claim(nextOwner) {
      owner = nextOwner
      generation += 1
      return { owner, generation }
    },
    invalidate() {
      owner = null
      generation += 1
    },
    owns(token) {
      return Boolean(token && token.owner === owner && token.generation === generation)
    },
  }
}

export async function settleOwned(owner, token, task, { success, failure, settled } = {}) {
  try {
    const value = await task
    if (!owner.owns(token)) return
    success?.(value)
  } catch (error) {
    if (!owner.owns(token)) return
    failure?.(error)
  }
  if (owner.owns(token)) settled?.()
}

export function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ''))
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

export function safeFundReportUrl(value) {
  try {
    const url = new URL(String(value || ''))
    return url.protocol === 'https:' && url.hostname === 'pdf.dfcfw.com' ? url.href : null
  } catch {
    return null
  }
}

const positiveFundEvents = new Set(['CONFIRMED_NEW', 'CONFIRMED_ADD', 'ENTERED_TOP10'])
const negativeFundEvents = new Set(['CONFIRMED_EXIT', 'CONFIRMED_REDUCE', 'DROPPED_FROM_TOP10'])

export function filterFundRows(rows, query, mode) {
  const needle = String(query || '').trim().toLowerCase()
  return rows.filter(row => {
    if (mode === 'consensus-positive' && row.direction !== 'positive') return false
    if (mode === 'consensus-negative' && row.direction !== 'negative') return false
    if (mode === 'events-positive' && !positiveFundEvents.has(row.event_type)) return false
    if (mode === 'events-negative' && !negativeFundEvents.has(row.event_type)) return false
    if (!needle) return true
    return `${row.code || row.stock_code || ''} ${row.name || row.stock_name || ''} ${row.industry || ''} ${row.fund_name || ''} ${row.manager_name || ''}`.toLowerCase().includes(needle)
  })
}

export function fundEventText(event) {
  const labels = {
    CONFIRMED_NEW: '确认新建仓',
    CONFIRMED_ADD: '确认增持',
    ENTERED_TOP10: '新进前十（不等于首次买入）',
    CONFIRMED_REDUCE: '确认减持',
    CONFIRMED_EXIT: '确认清仓',
    DROPPED_FROM_TOP10: '退出前十（不等于清仓）',
    UNCHANGED: '持股数量基本不变',
    CENSORED: '披露范围不足，无法判断',
  }
  return `${labels[event.event_type] || event.event_type || '未知事件'} · ${event.previous_scope || 'UNKNOWN'} → ${event.current_scope || 'UNKNOWN'}`
}

export function isNavigableStockCode(code, stocks) {
  return stocks.some(stock => stock.code === code)
}

export function joinRankingRows(rows, stocks) {
  const byCode = new Map(stocks.map(stock => [stock.code, stock]))
  return rows.flatMap(row => {
    const stock = byCode.get(row.code)
    return stock ? [{ ...stock, ...row }] : []
  })
}

export function filterRankingRows(rows, query, {
  minMarketCap = null,
  maxMarketCap = null,
  minPeg = null,
  maxPeg = null,
} = {}) {
  const needle = String(query || '').trim().toLowerCase()
  const inRange = (value, min, max) => {
    const active = min !== null || max !== null
    if (!active) return true
    const number = Number(value)
    if (value === null || value === undefined || !Number.isFinite(number)) return false
    return (min === null || number >= min) && (max === null || number <= max)
  }
  return rows.filter(row => (
    (!needle || `${row.code} ${row.name} ${row.industry || ''} ${row.pinyin || ''}`.toLowerCase().includes(needle))
    && inRange(row.market_cap, minMarketCap, maxMarketCap)
    && inRange(row.peg, minPeg, maxPeg)
  ))
}

export function rankRankingRows(rows, kind) {
  const metric = kind === 'dividend' ? 'yield' : kind === 'peg' ? 'peg' : 'score'
  let prior = null
  let rank = 0
  return rows.map((row, index) => {
    const raw = row[metric]
    const value = Number(raw)
    if (raw === null || raw === undefined || !Number.isFinite(value)) return { ...row, rank: null }
    if (prior === null || value !== prior) rank = index + 1
    prior = value
    return { ...row, rank }
  })
}

export function rankingMetricText(kind, row, years) {
  const valuation = `市值 ${formatMoney(row.market_cap)}`
  if (kind === 'peg') {
    const marketCap = `市值 ${formatMoney(row.market_cap)}`
    const growthLabel = years === 1 ? '1年归母净利增长率' : `${years}年归母净利CAGR`
    const pe = row.pe_ratio == null ? '—' : Number(row.pe_ratio).toFixed(2)
    const growth = row.profit_growth_pct == null ? '—' : `${Number(row.profit_growth_pct).toFixed(2)}%`
    const result = row.peg == null
      ? `PEG不可计算（${row.invalid_reason || '数据不足'}）`
      : `PEG ${Number(row.peg).toFixed(2)}`
    return `${marketCap} · 动态PE ${pe} ÷ ${growthLabel} ${growth} = ${result} · 归母净利润：${row.start_year}年 ${formatMoney(row.start_profit)} → ${row.end_year}年 ${formatMoney(row.end_profit)}`
  }
  if (kind === 'dividend') {
    const price = row.price == null ? '—' : `¥${Number(row.price).toFixed(2)}`
    return `${valuation} · ${years}年中${row.dividend_years}年现金分红 · 当前价 ${price}`
  }
  const percent = (value, signed = false) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
    const number = Number(value)
    return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
  }
  return `${valuation} · ${years}年营收CAGR ${percent(row.revenue_cagr, true)} · 归母净利CAGR ${percent(row.profit_cagr, true)} · 平均ROE ${percent(row.avg_roe)} · 盈利${row.profitable_years}/${years}年`
}

export function rankingValueText(kind, row) {
  if (kind === 'peg') {
    return row.peg == null
      ? { value: '—', label: '不可计算' }
      : { value: Number(row.peg).toFixed(2), label: 'PEG' }
  }
  if (kind === 'dividend') {
    const value = row.yield == null ? '—' : `${Number(row.yield).toFixed(2)}%`
    return { value, label: '平均分红率' }
  }
  return { value: Number(row.score).toFixed(1), label: '综合得分' }
}

export function searchStocks(stocks, query, limit = 30) {
  const needle = String(query || '').trim().toLowerCase()
  if (!needle) return stocks.slice(0, limit)
  return stocks
    .filter(stock => `${stock.code} ${stock.name} ${stock.pinyin || ''}`.toLowerCase().includes(needle))
    .slice(0, limit)
}

export function classFor(value) {
  return Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : ''
}
