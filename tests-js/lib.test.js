import test from 'node:test'
import assert from 'node:assert/strict'
import {
  annualRows,
  createSingleFlightLoader,
  createViewOwner,
  deriveHighlights,
  escapeHtml,
  settleOwned,
  filterFundRows,
  filterRankingRows,
  finiteNumberText,
  formatMoney,
  fundDataUrl,
  fundStockEventsUrl,
  fundEventText,
  fundTrackingStatusText,
  isNavigableStockCode,
  joinRankingRows,
  rankingMetricText,
  rankingValueText,
  rankRankingRows,
  rankingUrl,
  safeFundReportUrl,
  safeHttpUrl,
  searchStocks,
  shardUrl,
} from '../public/lib.js'

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

test('formatMoney renders Chinese financial units', () => {
  assert.equal(formatMoney(1_250_000_000), '12.50亿')
  assert.equal(formatMoney(-23_000_000), '-2300.00万')
  assert.equal(formatMoney(null), '—')
})

test('annualRows keeps year reports in chronological order', () => {
  const rows = [
    { period: '2025-09-30', report_type: '三季报' },
    { period: '2024-12-31', report_type: '年报' },
    { period: '2025-12-31', report_type: '年报' },
  ]
  assert.deepEqual(annualRows(rows).map(x => x.period), ['2024-12-31', '2025-12-31'])
})

test('deriveHighlights uses latest financial and implemented dividend', () => {
  const data = {
    financials: [{ period: '2025-12-31', revenue: 100, net_profit: 20, roe: 18.2 }],
    dividends: [
      { period: '2025-12-31', progress: '董事会预案', cash_per_10: 10 },
      { period: '2024-12-31', progress: '实施分配', cash_per_10: 8 },
    ],
  }
  assert.deepEqual(deriveHighlights(data), { revenue: 100, netProfit: 20, roe: 18.2, lastCashPer10: 8 })
})

test('shardUrl is GitHub Pages subpath safe', () => {
  assert.equal(shardUrl('/a-share-finance/', '600519'), '/a-share-finance/data/companies/60/600519.json')
})

test('searchStocks matches code and Chinese name with a bounded result', () => {
  const stocks = [
    { code: '000001', name: '平安银行' },
    { code: '600519', name: '贵州茅台' },
    { code: '300750', name: '宁德时代' },
  ]
  assert.deepEqual(searchStocks(stocks, '6005').map(x => x.code), ['600519'])
  assert.deepEqual(searchStocks(stocks, '银行').map(x => x.code), ['000001'])
  assert.equal(searchStocks(stocks, '', 2).length, 2)
})

test('rankingUrl selects one precomputed ranking file for the active range', () => {
  assert.equal(
    rankingUrl('/a-share-finance/', 'dividend', 5),
    '/a-share-finance/data/rankings/dividend-5.json',
  )
  assert.equal(
    rankingUrl('/a-share-finance', 'performance', 10),
    '/a-share-finance/data/rankings/performance-10.json',
  )
})

test('ranking rows join stock identity and filter locally without extra data', () => {
  const stocks = [
    { code: '000001', name: '平安银行', industry: '银行', market_cap: 250e8, peg: 0.8 },
    { code: '600519', name: '贵州茅台', industry: '白酒', market_cap: 18_000e8, peg: 1.6 },
  ]
  const joined = joinRankingRows([
    { code: '600519', yield: 4.2 },
    { code: '000001', yield: 3.1 },
    { code: '999999', yield: 99 },
  ], stocks)

  assert.deepEqual(joined.map(row => row.name), ['贵州茅台', '平安银行'])
  assert.deepEqual(filterRankingRows(joined, '银行').map(row => row.code), ['000001'])
  assert.deepEqual(filterRankingRows(joined, '6005').map(row => row.code), ['600519'])
  assert.deepEqual(filterRankingRows(joined, '', { minMarketCap: 200e8, maxMarketCap: 500e8 }).map(row => row.code), ['000001'])
  assert.deepEqual(filterRankingRows(joined, '', { minPeg: 1, maxPeg: 2 }).map(row => row.code), ['600519'])
  assert.deepEqual(
    filterRankingRows([...joined, { code: '000002', name: '无PEG', market_cap: 300e8, peg: null }], '', { maxPeg: 1 }).map(row => row.code),
    ['000001'],
  )
})

test('period-specific ranking values override stock-index valuation fields', () => {
  const rows = joinRankingRows(
    [{ code: '000001', peg: 0.5, profit_growth_pct: 40 }],
    [{ code: '000001', name: '平安银行', peg: 1.8, peg_growth_yoy: 10 }],
  )

  assert.equal(rows[0].peg, 0.5)
  assert.equal(rows[0].profit_growth_pct, 40)
})

test('ranking rows use competition ranks for equal metrics', () => {
  const rows = rankRankingRows([
    { code: '000001', yield: 5, score: 90 },
    { code: '000002', yield: 5, score: 90 },
    { code: '000003', yield: 4, score: 80 },
  ], 'dividend')

  assert.deepEqual(rows.map(row => row.rank), [1, 1, 3])
})

test('PEG ranking sorts ascending and leaves unavailable rows unranked', () => {
  const rows = rankRankingRows([
    { code: '000001', peg: 0.5 },
    { code: '000002', peg: 0.5 },
    { code: '000003', peg: 0.8 },
    { code: '000004', peg: null, invalid_reason: '动态市盈率缺失' },
  ], 'peg')

  assert.deepEqual(rows.map(row => row.rank), [1, 1, 3, null])
})

test('PEG metric text shows every formula component and profit endpoints', () => {
  assert.equal(
    rankingMetricText('peg', {
      market_cap: 250e8,
      pe_ratio: 20,
      profit_growth_pct: 25.99,
      peg: 0.77,
      start_year: 2022,
      end_year: 2025,
      start_profit: 100e8,
      end_profit: 200e8,
      invalid_reason: null,
    }, 3),
    '市值 250.00亿 · 动态PE 20.00 ÷ 3年归母净利CAGR 25.99% = PEG 0.77 · 归母净利润：2022年 100.00亿 → 2025年 200.00亿',
  )
})

test('unavailable PEG text keeps available formula components and reason', () => {
  assert.equal(
    rankingMetricText('peg', {
      market_cap: 250e8,
      pe_ratio: null,
      profit_growth_pct: 25.99,
      peg: null,
      start_year: 2022,
      end_year: 2025,
      start_profit: 100e8,
      end_profit: 200e8,
      invalid_reason: '动态市盈率缺失',
    }, 3),
    '市值 250.00亿 · 动态PE — ÷ 3年归母净利CAGR 25.99% = PEG不可计算（动态市盈率缺失） · 归母净利润：2022年 100.00亿 → 2025年 200.00亿',
  )
})

test('PEG value display distinguishes calculable and unavailable rows', () => {
  assert.deepEqual(rankingValueText('peg', { peg: 0.77 }), { value: '0.77', label: 'PEG' })
  assert.deepEqual(rankingValueText('peg', { peg: null }), { value: '—', label: '不可计算' })
})

test('performance metric text explains the selected period and all score inputs', () => {
  assert.equal(
    rankingMetricText('performance', {
      market_cap: 250e8,
      peg: 1.35,
      revenue_cagr: 17.8,
      profit_cagr: 18.17,
      avg_roe: 31.92,
      profitable_years: 10,
    }, 10),
    '市值 250.00亿 · 10年营收CAGR +17.80% · 归母净利CAGR +18.17% · 平均ROE 31.92% · 盈利10/10年',
  )
})

test('fundDataUrl is GitHub Pages subpath safe', () => {
  assert.equal(fundDataUrl('/a-share-finance/', 'consensus'), '/a-share-finance/data/funds/consensus.json')
  assert.equal(fundDataUrl('/a-share-finance', 'events'), '/a-share-finance/data/funds/events.json')
})

test('fundStockEventsUrl loads only one company event shard', () => {
  assert.equal(
    fundStockEventsUrl('/a-share-finance/', '600519'),
    '/a-share-finance/data/funds/stocks/60/600519.json',
  )
})

test('fund tracking status distinguishes failed refreshes from pending coverage', () => {
  assert.deepEqual(fundTrackingStatusText('tracked'), { label: '持仓已更新', short: '已更新' })
  assert.deepEqual(fundTrackingStatusText('update_failed'), { label: '更新失败，沿用旧数据', short: '更新失败' })
  assert.deepEqual(fundTrackingStatusText('pending'), { label: '等待轮询', short: '待更新' })
})

test('filterFundRows separates positive and negative consensus and searches locally', () => {
  const rows = [
    { code: '600001', name: '正向公司', direction: 'positive', industry: '机械' },
    { code: '600002', name: '负向公司', direction: 'negative', industry: '医药' },
  ]
  assert.deepEqual(filterFundRows(rows, '', 'consensus-positive').map(row => row.code), ['600001'])
  assert.deepEqual(filterFundRows(rows, '', 'consensus-negative').map(row => row.code), ['600002'])
  assert.deepEqual(filterFundRows(rows, '机械', 'consensus-positive').map(row => row.code), ['600001'])
})

test('fund event text states evidence scope instead of calling every top10 entry a new position', () => {
  assert.equal(
    fundEventText({ event_type: 'ENTERED_TOP10', previous_scope: 'TOP10', current_scope: 'TOP10' }),
    '新进前十（不等于首次买入） · TOP10 → TOP10',
  )
  assert.equal(
    fundEventText({ event_type: 'CONFIRMED_NEW', previous_scope: 'FULL', current_scope: 'TOP10' }),
    '确认新建仓 · FULL → TOP10',
  )
})

test('fund events navigate only to stocks present in the current A-share directory', () => {
  const stocks = [{ code: '600519' }, { code: '000001' }]
  assert.equal(isNavigableStockCode('600519', stocks), true)
  assert.equal(isNavigableStockCode('00700', stocks), false)
  assert.equal(isNavigableStockCode('退市股', stocks), false)
  assert.equal(isNavigableStockCode('600999', stocks), false)
})

test('safeHttpUrl accepts report web links and rejects executable schemes', () => {
  assert.equal(safeHttpUrl('https://example.test/report.pdf'), 'https://example.test/report.pdf')
  assert.equal(safeHttpUrl('http://example.test/report.pdf'), 'http://example.test/report.pdf')
  assert.equal(safeHttpUrl('javascript:alert(1)'), null)
  assert.equal(safeHttpUrl('data:text/html,boom'), null)
})

test('fund report URLs allow only HTTPS links on the exact Eastmoney PDF host', () => {
  assert.equal(safeFundReportUrl('https://pdf.dfcfw.com/pdf/H2_AN.pdf'), 'https://pdf.dfcfw.com/pdf/H2_AN.pdf')
  assert.equal(safeFundReportUrl('http://pdf.dfcfw.com/report.pdf'), null)
  assert.equal(safeFundReportUrl('https://pdf.dfcfw.com.evil.test/report.pdf'), null)
  assert.equal(safeFundReportUrl('https://pdf.dfcfw.com@evil.test/report.pdf'), null)
  assert.equal(safeFundReportUrl('https://example.test/report.pdf'), null)
})

test('HTML and numeric helpers neutralize untrusted values before template insertion', () => {
  assert.equal(escapeHtml(`<img src=x onerror='boom'>&\"`), '&lt;img src=x onerror=&#39;boom&#39;&gt;&amp;&quot;')
  assert.equal(finiteNumberText('12.345', 2), '12.35')
  assert.equal(finiteNumberText('<img src=x>'), '—')
  assert.equal(finiteNumberText(Infinity), '—')
})

test('single-flight loader reuses the same in-flight promise and cached result', async () => {
  const pending = deferred()
  let calls = 0
  const loader = createSingleFlightLoader(() => { calls += 1; return pending.promise })

  const first = loader.load()
  const second = loader.load()
  assert.equal(first, second)
  assert.equal(calls, 1)

  pending.resolve({ rows: [1] })
  assert.deepEqual(await first, { rows: [1] })
  assert.deepEqual(await loader.load(), { rows: [1] })
  assert.equal(calls, 1)
})

test('view ownership invalidates older opens, closes, and company panel renders', () => {
  const owner = createViewOwner()
  const firstOpen = owner.claim('fund-view')
  const repeatedOpen = owner.claim('fund-view')
  assert.equal(owner.owns(firstOpen), false)
  assert.equal(owner.owns(repeatedOpen), true)

  owner.invalidate()
  assert.equal(owner.owns(repeatedOpen), false)

  const companyA = owner.claim('company:000001')
  const companyB = owner.claim('company:600519')
  assert.equal(owner.owns(companyA), false)
  assert.equal(owner.owns(companyB), true)
})

test('owned async work suppresses stale render, error, and scroll effects', async () => {
  for (const outcome of ['resolve', 'reject']) {
    const owner = createViewOwner()
    const token = owner.claim('fund-view')
    const pending = deferred()
    const effects = []
    const work = settleOwned(owner, token, pending.promise, {
      success: () => effects.push('render'),
      failure: () => effects.push('error'),
      settled: () => effects.push('scroll'),
    })

    owner.claim('ranking-view')
    if (outcome === 'resolve') pending.resolve('ok')
    else pending.reject(new Error('late failure'))
    await work
    assert.deepEqual(effects, [])
  }
})
