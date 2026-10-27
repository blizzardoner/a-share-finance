import test from 'node:test'
import assert from 'node:assert/strict'
import { annualRows, deriveHighlights, formatMoney, searchStocks, shardUrl } from '../public/lib.js'

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
