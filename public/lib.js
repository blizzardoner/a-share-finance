const compact = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })

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

export function joinRankingRows(rows, stocks) {
  const byCode = new Map(stocks.map(stock => [stock.code, stock]))
  return rows.flatMap(row => {
    const stock = byCode.get(row.code)
    return stock ? [{ ...row, ...stock }] : []
  })
}

export function filterRankingRows(rows, query) {
  const needle = String(query || '').trim().toLowerCase()
  if (!needle) return rows
  return rows.filter(row => `${row.code} ${row.name} ${row.industry || ''} ${row.pinyin || ''}`.toLowerCase().includes(needle))
}

export function rankRankingRows(rows, kind) {
  const metric = kind === 'dividend' ? 'yield' : 'score'
  let prior = null
  let rank = 0
  return rows.map((row, index) => {
    const value = Number(row[metric])
    if (index === 0 || value !== prior) rank = index + 1
    prior = value
    return { ...row, rank }
  })
}

export function rankingMetricText(kind, row, years) {
  if (kind === 'dividend') {
    const price = row.price == null ? '—' : `¥${Number(row.price).toFixed(2)}`
    return `${years}年中${row.dividend_years}年现金分红 · 当前价 ${price}`
  }
  const percent = (value, signed = false) => {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
    const number = Number(value)
    return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
  }
  return `${years}年营收CAGR ${percent(row.revenue_cagr, true)} · 归母净利CAGR ${percent(row.profit_cagr, true)} · 平均ROE ${percent(row.avg_roe)} · 盈利${row.profitable_years}/${years}年`
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
