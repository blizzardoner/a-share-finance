# A股透镜

面向手机浏览的免费静态网站，展示沪深北当前全部 A 股公司的关键财务指标、资产负债、现金流和历年分红。数据由 GitHub Actions 每日从东方财富公开接口抓取并发布到 GitHub Pages，不需要服务器、数据库或 API 密钥。

**线上地址：** <https://blizzardoner.github.io/a-share-finance/>

## 数据范围

- 当前 A 股公司目录：代码、名称、交易所、行业、价格和市值
- 2021 年以来财务报告：营收、归母净利润、扣非净利润及同比
- EPS、每股净资产、ROE、ROIC、毛利率、净利率
- 总资产、总负债、股东权益、资产负债率、流动/速动比率
- 经营、投资和融资现金流以及净利现金比
- 分红方案、进度、股权登记日、除权日、每 10 股现金分红/送转
- 银行专属指标：存款、贷款、不良贷款率等（字段存在时展示）

## 免费部署架构

```text
GitHub Actions（每日一次）
  └─ 东方财富公开接口
       ├─ 当前上市公司目录
       ├─ 主要财务指标
       └─ 分红明细
            ↓
       按公司拆分的静态 JSON
            ↓
GitHub Pages + 手机 PWA
```

网站不在浏览器中直接高频访问东方财富。定时任务集中、限速抓取后生成：

- `data/stocks.json`：公司搜索目录
- `data/companies/60/600519.json`：单家公司数据分片

手机只下载搜索目录和当前查看的公司文件，避免一次加载全市场财务数据。

## 本地运行

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .

# 抓取三家公司用于开发预览
.venv/Scripts/ashare-finance.exe \
  --output public/data \
  --since 2021-01-01 \
  --codes 000001,300750,600519

python -m http.server 4173 --directory public
```

浏览 <http://127.0.0.1:4173>。

## 抓取全市场

```bash
.venv/Scripts/ashare-finance.exe --output public/data --since 2021-01-01
```

当前上市公司目录以东方财富行情接口为准，避免把历史退市公司混入现有 A 股列表。财务和分红接口按 500 条分页，客户端自带请求间隔、指数退避和失败重试。

## 测试

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -v
npm test
npm run check
```

## 自动更新

`.github/workflows/pages.yml`：

- 每天 UTC 20:30（北京时间次日 04:30）自动抓取并部署；
- 推送到 `main` 或手动运行工作流时也会更新；
- 使用 GitHub Pages 官方 artifact 部署，不把每日生成的大量 JSON 提交回 Git 历史。

## PWA

网站可在 iPhone Safari 中使用“分享 → 添加到主屏幕”。服务工作线程会缓存应用外壳、公司目录和已浏览公司的数据；数据在线时优先刷新，离线时回退到缓存。

## 数据与风险说明

数据来源于东方财富公开页面及其网页使用的公开接口，仅用于个人研究和信息整理。接口字段和页面结构可能变化；自动任务设置了限速，不绕过登录、验证码或访问控制。财务数据以交易所公告和上市公司正式报告为准，本项目不构成投资建议。

## License

MIT
