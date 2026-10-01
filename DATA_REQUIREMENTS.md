# Data requirements for full fundamental backtesting

The free Vnstock package is sufficient for current technical analysis and can provide selected current and historical financial statements. It is not treated as sufficient for point-in-time fundamental backtesting until the fields below are available and verified.

## Required datasets

### Financial statements

One record per symbol, metric and report version:

- `symbol`
- `report_period`
- `published_at`
- `available_from`
- `restated_at`
- `company_type`
- `metric_id`
- `value`
- `unit`
- `source`

Required metrics include revenue, net income, EPS, EBIT, EBITDA, interest expense, equity, debt, operating cash flow and outstanding shares.

### Classification and listing history

- Symbol and exchange
- ICB industry code valid at each date
- Company type: Regular, Bank, Securities or Insurance
- Listing, delisting, transfer and symbol-change dates
- Historical index membership when testing VN30 or another index universe

### Corporate actions and market data

- Adjusted daily OHLCV
- Cash and stock dividends
- Splits, bonus shares and rights issues
- Ex-date and adjustment factor

### Benchmark and risk-free rate

- VNINDEX adjusted daily series
- Historical government bond yield or another documented risk-free proxy

## Acceptance checks

- At least five years of history
- No future report available before `available_from`
- Restated reports preserve the original version
- Delisted stocks remain in the historical universe
- Values identify currency and scale
- Corporate-action adjustment can be reconciled against raw close prices

