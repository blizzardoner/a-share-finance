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
