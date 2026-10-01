# Chapter 29 – Inventory Engine

## 29.1 Purpose

The Inventory Engine manages asset inventory across venues and accounts from the perspective of liquidity provision and multi-venue trading. It tracks where each asset is held, computes inventory relative to targets, provides inventory-skew inputs to market-making and arbitrage strategies, and recommends (but never executes, CON-060) rebalancing transfers between venues.

## 29.2 Domain Entities

### 29.2.1 InventoryPool

| Attribute | Type | Constraints |
|---|---|---|
| name | String(100) | Unique within workspace |
| asset | String | Asset code (e.g. BTC, USDT, AAPL) |
| member_accounts | List of TradingAccount | ≥ 1 |
| target_quantity | Decimal | Desired total inventory |
| min_quantity / max_quantity | Decimal | Bounds |
| per_account_targets | List of (account_id, target, min, max) | Optional |
| valuation_currency | Currency | Required |

### 29.2.2 InventorySnapshot

| Attribute | Description |
|---|---|
| pool_id, as_of | Identity |
| per_account | (account_id, total, available, locked, in_open_orders, pending_transfer) |
| total_quantity | Σ per-account totals |
| deviation | total − target |
| deviation_ratio | deviation / (max − target) if deviation > 0 else deviation / (target − min) |
| inventory_value | total × reference price |

### 29.2.3 TransferRecommendation

| Attribute | Description |
|---|---|
| asset, from_account, to_account | Movement |
| quantity | Recommended amount |
| reason | Below minimum, above maximum, rebalance to target |
| estimated_cost | Network or withdrawal fee estimate |
| status | PROPOSED, ACKNOWLEDGED, COMPLETED_EXTERNALLY, DISMISSED |

## 29.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-29001 | The system shall allow users to define inventory pools per asset across one or more accounts with targets and bounds. | M | T |
| FR-29002 | The system shall compute inventory snapshots in real time from balances (Chapter 23) and working orders. | M | T |
| FR-29003 | The system shall expose the normalized inventory deviation ratio in the range [−1, +1] (clamped) to strategies via the context service for use in quote skewing. | M | T |
| FR-29004 | The system shall raise alerts when a pool or account crosses its minimum or maximum bound. | M | T |
| FR-29005 | The system shall generate transfer recommendations to restore per-account targets, minimizing the number of transfers, and shall never initiate asset transfers (CON-060). | S | T |
| FR-29006 | The system shall allow users to record that a recommended transfer was completed externally; the system shall confirm completion when the balance change is observed. | S | T |
| FR-29007 | The system shall track inventory P&L for market-making strategies decomposed into spread capture (realized from round trips) and inventory mark-to-market (from holding net inventory). | S | T |
| FR-29008 | The system shall display an inventory dashboard showing per pool and per account holdings, deviation, and value, with history charts. | M | D |
| FR-29009 | The system shall provide inventory half-life (average time to revert to target) per pool over configurable lookback. | C | T |

## 29.4 Business Rules

| ID | Rule |
|---|---|
| BR-29-01 | Quantity in open SELL orders is counted in `in_open_orders` and excluded from available inventory. |
| BR-29-02 | Spread capture P&L for a matched buy/sell pair = matched quantity × (sell price − buy price) − fees; the unmatched remainder contributes to inventory mark-to-market. |

## 29.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-29001 | Given target 10 BTC, max 12, current 11, then deviation ratio = 0.5. | FR-29003 |
| AC-29002 | Given a pool falling below its minimum, then an alert is raised and a transfer recommendation is created; no transfer is executed. | FR-29004, FR-29005 |

---

*End of Chapter 29 – Inventory Engine*
