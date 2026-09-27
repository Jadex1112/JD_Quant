# Chapter 23 – Portfolio Engine

## 23.1 Purpose

The Portfolio Management Engine (PME) aggregates positions, balances, and cash across accounts, strategies, and venues into portfolios; values them in a reporting currency; computes net asset value (NAV), exposures, and allocations; tracks capital flows; and provides the portfolio view consumed by risk, analytics, reporting, and optimization.

Position keeping at the instrument level is specified in Chapter 28 (Position Engine); multi-venue asset inventory in Chapter 29 (Inventory Engine). This chapter specifies the portfolio aggregation layer above them.

## 23.2 Scope and Actors

### In Scope

- Portfolio definition and hierarchy
- Cash and balance tracking
- Valuation and NAV computation
- Exposure computation (gross, net, long, short, by dimension)
- Allocation targets and drift
- Capital flows (deposits, withdrawals, transfers as recorded events; no fund movement per CON-060)
- Benchmark assignment
- Portfolio snapshots and history

### Actors

| Actor | Interaction |
|---|---|
| Portfolio Manager | Defines portfolios, targets, benchmarks; reviews NAV and exposures |
| RMS | Consumes exposures |
| Analytics / Reporting | Consumes snapshots and history |
| Portfolio Optimizer | Consumes holdings; proposes target weights |

## 23.3 Domain Entities

### 23.3.1 Portfolio

| Attribute | Type | Constraints |
|---|---|---|
| name | String(100) | Unique within workspace |
| type | Enum: ACCOUNT, STRATEGY, COMPOSITE, MODEL | Required |
| parent_id | Reference → Portfolio | Optional; hierarchy depth ≤ 5 |
| members | List of (account_id or deployment_id or portfolio_id) | Per type |
| reporting_currency | Currency | Required |
| benchmark_id | Reference → Benchmark | Optional |
| inception_date | Date | Required |
| status | Enum: ACTIVE, CLOSED | Default ACTIVE |

### 23.3.2 Balance

| Attribute | Type | Description |
|---|---|---|
| account_id | Reference | Account |
| asset | String | Currency or asset code |
| total | Decimal | Total balance reported by venue |
| available | Decimal | Free for trading |
| locked | Decimal | Reserved by open orders or margin |
| borrowed | Decimal | Margin borrow |
| interest_accrued | Decimal | Accrued borrow interest |
| source | Enum: VENUE, DERIVED | Venue-reported or computed |
| as_of | Timestamp | Last update |

### 23.3.3 PortfolioSnapshot

| Attribute | Description |
|---|---|
| portfolio_id, as_of | Identity |
| nav | Net asset value in reporting currency |
| cash | Cash and cash equivalents |
| market_value_long / market_value_short | Position market values |
| gross_exposure / net_exposure | See BR-23-03 |
| leverage | gross_exposure / nav |
| unrealized_pnl / realized_pnl_day / fees_day / funding_day | Components |
| holdings | List of (instrument_id, quantity, price, market_value, weight) |
| valuation_status | COMPLETE or PARTIAL (missing prices listed) |

### 23.3.4 CapitalFlow

| Attribute | Type | Description |
|---|---|---|
| portfolio_id / account_id | Reference | Target |
| flow_type | Enum: DEPOSIT, WITHDRAWAL, TRANSFER_IN, TRANSFER_OUT, FEE, INTEREST, DIVIDEND, ADJUSTMENT | Required |
| asset / amount | Money | Required |
| effective_at | Timestamp | Required |
| source | Enum: VENUE_DETECTED, MANUAL | Required |

### 23.3.5 AllocationTarget

| Attribute | Description |
|---|---|
| portfolio_id | Target portfolio |
| targets | List of (dimension key, target weight, min weight, max weight) |
| dimension | INSTRUMENT, ASSET_CLASS, SECTOR, STRATEGY, VENUE, CURRENCY |
| rebalance_threshold | Absolute drift triggering a rebalance proposal |

## 23.4 Functional Requirements – Portfolio Definition

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-23001 | The system shall automatically create an ACCOUNT portfolio for each trading account and a STRATEGY portfolio for each deployment. | M | T |
| FR-23002 | The system shall allow authorized users to create COMPOSITE portfolios aggregating accounts, deployments, or other portfolios, and MODEL portfolios with hypothetical holdings. | M | T |
| FR-23003 | The system shall prevent cycles in portfolio hierarchies and limit depth to 5. | M | T |
| FR-23004 | The system shall allow assigning a benchmark (instrument, index, or custom weighted basket) to a portfolio. | M | T |

## 23.5 Functional Requirements – Balances and Cash

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-23010 | The system shall synchronize balances from each venue on connection, on balance-update events, and at a configurable interval (default 60 s). | M | T |
| FR-23011 | The system shall maintain derived balances updated in real time from fills and fees between venue synchronizations, and reconcile them to venue balances on each synchronization (CON-042). | M | T |
| FR-23012 | The system shall raise a warning when the derived and venue balance for an asset differ by more than the configured tolerance (default 0.01% or one lot, whichever is greater) after reconciliation. | M | T |
| FR-23013 | The system shall detect deposits and withdrawals from venue balance history where available and record them as CapitalFlows; otherwise unexplained balance changes shall be recorded as ADJUSTMENT pending user classification. | M | T |
| FR-23014 | The system shall allow manual recording of capital flows for accounts whose venues do not report them. | M | T |

## 23.6 Functional Requirements – Valuation

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-23020 | The system shall value each holding using the reference price rule BR-20-04 and convert to the reporting currency using the most recent FX rate (DEP-202). | M | T |
| FR-23021 | The system shall recompute real-time NAV for every portfolio at a configurable interval (default 1 s) and on every fill. | M | T |
| FR-23022 | The system shall mark a snapshot PARTIAL when any holding lacks a non-stale price or FX rate and list the missing items. | M | T |
| FR-23023 | The system shall persist portfolio snapshots at configurable intervals (default every 1 minute intraday and at 00:00 UTC daily end-of-day) and on demand. | M | T |
| FR-23024 | The system shall support configurable end-of-day valuation times per portfolio (for example venue close for equity portfolios). | S | T |
| FR-23025 | The system shall value derivatives positions by unrealized P&L relative to entry price (futures, perpetuals) and by premium mark-to-market (options), including accrued funding. | M | T |
| FR-23026 | The system shall compute NAV of a composite portfolio as the sum of member NAVs converted to the composite's reporting currency, without double-counting shared members. | M | T |

## 23.7 Functional Requirements – Exposure and Allocation

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-23040 | The system shall compute gross, net, long, and short exposure per portfolio and by dimension (instrument, asset class, sector, venue, currency, strategy). | M | T |
| FR-23041 | The system shall compute the beta-adjusted exposure against the portfolio benchmark using a configurable lookback (default 90 days of daily returns). | S | T |
| FR-23042 | The system shall compute current weights versus allocation targets and report drift per dimension key. | M | T |
| FR-23043 | The system shall generate a rebalance proposal (list of orders) when drift exceeds the rebalance threshold; proposals shall require user approval before submission unless an automation policy covers rebalancing. | S | T |
| FR-23044 | The system shall display currency exposure and allow users to see NAV in any supported currency. | M | D |

## 23.8 Functional Requirements – History

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-23060 | The system shall provide NAV, exposure, and holdings history for any portfolio over any date range within retention. | M | T |
| FR-23061 | The system shall reconstruct a portfolio's holdings at any historical instant from positions, fills, and capital flows. | M | T |
| FR-23062 | The system shall provide time-weighted and money-weighted returns to the Performance module (Chapter 32), adjusting for capital flows. | M | T |

## 23.9 Business Rules

| ID | Rule |
|---|---|
| BR-23-01 | NAV = Σ(cash balances in reporting currency) + Σ(market value of long spot holdings) − Σ(market value of short spot holdings and borrowed assets) + Σ(unrealized P&L of derivatives) − accrued liabilities. |
| BR-23-02 | Weight of holding = market value / NAV (signed). |
| BR-23-03 | Gross exposure = Σ\|notional\|; net exposure = Σ(signed notional); notional for derivatives = quantity × price × contract_multiplier. |
| BR-23-04 | Capital flows do not count as P&L. |
| BR-23-05 | FX rates older than the configured FX staleness threshold (default 1 h) mark the valuation PARTIAL. |

## 23.10 Events

| Event | Description |
|---|---|
| `portfolio.snapshot` | Periodic snapshot |
| `portfolio.nav.updated` | Real-time NAV change (conflated) |
| `portfolio.balance.mismatch` | Reconciliation difference beyond tolerance |
| `portfolio.capitalflow.recorded` | Capital flow recorded |
| `portfolio.drift.exceeded` | Allocation drift threshold breached |

## 23.11 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-23001 | Given holdings with known prices and FX rates, then NAV equals the independent calculation per BR-23-01 exactly. | FR-23020, FR-23021 |
| AC-23002 | Given a deposit of 1,000 USD, then NAV increases by 1,000 USD and daily return is unaffected. | FR-23013, BR-23-04 |
| AC-23003 | Given a missing price for one holding, then the snapshot is PARTIAL and names the instrument. | FR-23022 |
| AC-23004 | Given a composite of two accounts, then its NAV equals the sum of member NAVs in the composite currency. | FR-23026 |

---

*End of Chapter 23 – Portfolio Engine*
