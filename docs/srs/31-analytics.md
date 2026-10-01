# Chapter 31 – Analytics

## 31.1 Purpose

The Analytics & Attribution Engine (AAE) provides analytical views over trading, execution, portfolio, strategy, market, AI, and operational data. It computes derived analytics, P&L attribution, and aggregations, and powers dashboards and ad-hoc analysis. Standard performance metrics are specified in Chapter 32 and scheduled documents in Chapter 37.

## 31.2 Analytics Domains

| Domain | Scope |
|---|---|
| Trade Analytics | Round trips, win/loss distribution, holding periods, MAE/MFE |
| Execution Analytics | Slippage, implementation shortfall, fill rates, latency, venue comparison |
| Portfolio Analytics | Exposure, allocation, concentration, attribution |
| Strategy Analytics | Per-strategy performance, capacity, decay |
| Market Analytics | Volatility regimes, correlation, liquidity, spreads, volume profiles |
| AI Analytics | Model prediction accuracy, drift, feature importance |
| Operational Analytics | Uptime, error rates, latency distributions |

## 31.3 Functional Requirements – Trade and Execution Analytics

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-31001 | The system shall compute round-trip trades from fills for live and paper accounts using the account's cost basis method. | M | T |
| FR-31002 | The system shall provide trade statistics: count, win rate, average win, average loss, payoff ratio, expectancy, largest win/loss, average holding period, and distribution histograms. | M | T |
| FR-31003 | The system shall provide execution analytics per order, deployment, algorithm, venue, instrument, and time bucket: average slippage, implementation shortfall (BR-22-02), fill rate for limit orders, cancel-to-fill ratio, maker/taker ratio, and latency percentiles per hop (FR-22061). | M | T |
| FR-31004 | The system shall provide venue comparison of execution quality for the same instrument across venues. | S | D |
| FR-31005 | The system shall compute markouts: price movement after fills at 1 s, 5 s, 30 s, 60 s, and 300 s, to measure adverse selection. | S | T |

## 31.4 Functional Requirements – P&L Attribution

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-31020 | The system shall decompose P&L for any portfolio and period by strategy, instrument, asset class, venue, and currency. | M | T |
| FR-31021 | The system shall decompose P&L into components: trading (realized), mark-to-market (unrealized change), fees, funding, borrow interest, and FX translation. | M | T |
| FR-31022 | The system shall perform Brinson-style allocation/selection attribution against the benchmark for portfolios with sector or asset-class classification. | S | T |
| FR-31023 | The system shall perform factor-based attribution against a configurable factor model (for example market, size, value, momentum) when factor return data is available. | C | T |
| FR-31024 | The system shall guarantee that attribution components sum to total P&L within rounding tolerance of one minor currency unit. | M | T |

## 31.5 Functional Requirements – Market Analytics

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-31040 | The system shall compute realized volatility (close-to-close, Parkinson, Garman-Klass) per instrument over configurable windows. | M | T |
| FR-31041 | The system shall compute rolling correlation matrices for user-selected instrument sets. | M | T |
| FR-31042 | The system shall compute liquidity analytics: average spread, depth at N bps, average daily volume, and intraday volume profile. | M | T |
| FR-31043 | The system shall classify market regimes (for example trending/ranging, high/low volatility) using configurable rule-based or model-based classifiers and expose the current regime to strategies. | S | T |

## 31.6 Functional Requirements – Dashboards and Ad-hoc Analysis

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-31060 | The system shall provide configurable dashboards composed of widgets (charts, tables, KPIs, heatmaps) bound to analytics queries, savable per user and shareable within the workspace. | M | D |
| FR-31061 | The system shall provide role-default dashboards for each user class in Chapter 13. | M | D |
| FR-31062 | The system shall allow filtering every analytics view by time range, account, portfolio, deployment, instrument, and tags. | M | T |
| FR-31063 | The system shall provide an ad-hoc query interface over analytics datasets with a governed schema, enforcing the user's data permissions. | S | T |
| FR-31064 | The system shall refresh real-time widgets at most every 1 s and at least every 10 s; historical widgets on demand. | M | T |
| FR-31065 | The system shall return analytics queries over 1 year of daily data for one portfolio within 3 s p95. | M | T |

## 31.7 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-31001 | Given a day's P&L attribution, then the sum of strategy components equals the total P&L within one minor unit. | FR-31024 |
| AC-31002 | Given fills with known arrival and fill prices, then slippage values match BR-22-03. | FR-31003 |

---

*End of Chapter 31 – Analytics*
