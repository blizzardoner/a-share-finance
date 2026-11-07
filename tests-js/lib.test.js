import test from 'node:test'
import assert from 'node:assert/strict'
import {
  annualRows,
  deriveHighlights,
  filterRankingRows,
  formatMoney,
  joinRankingRows,
  rankingMetricText,
  rankRankingRows,
  rankingUrl,
  searchStocks,
  shardUrl,
} from '../public/lib.js'

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
    { code: '000001', name: '平安银行', industry: '银行' },
    { code: '600519', name: '贵州茅台', industry: '白酒' },
  ]
  const joined = joinRankingRows([
    { code: '600519', yield: 4.2 },
    { code: '000001', yield: 3.1 },
    { code: '999999', yield: 99 },
  ], stocks)

  assert.deepEqual(joined.map(row => row.name), ['贵州茅台', '平安银行'])
  assert.deepEqual(filterRankingRows(joined, '银行').map(row => row.code), ['000001'])
  assert.deepEqual(filterRankingRows(joined, '6005').map(row => row.code), ['600519'])
})

test('ranking rows use competition ranks for equal metrics', () => {
  const rows = rankRankingRows([
    { code: '000001', yield: 5, score: 90 },
    { code: '000002', yield: 5, score: 90 },
    { code: '000003', yield: 4, score: 80 },
  ], 'dividend')

  assert.deepEqual(rows.map(row => row.rank), [1, 1, 3])
})

test('performance metric text explains the selected period and all score inputs', () => {
  assert.equal(
    rankingMetricText('performance', {
      revenue_cagr: 17.8,
      profit_cagr: 18.17,
      avg_roe: 31.92,
      profitable_years: 10,
    }, 10),
    '10年营收CAGR +17.80% · 归母净利CAGR +18.17% · 平均ROE 31.92% · 盈利10/10年',
  )
})
