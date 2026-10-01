# Chapter 26 – Paper Trading

## 26.1 Purpose

The Paper Trading Engine (PTE) executes strategies and manual orders against live market data with simulated order execution, producing realistic results without financial risk. Paper trading is the mandatory forward-testing stage between backtesting and live trading (FR-24042).

## 26.2 Scope and Actors

### In Scope

- Paper trading accounts with virtual balances
- Simulated execution against real-time data using the execution simulator
- Paper deployments of strategies
- Manual paper orders
- Paper account reset and funding
- Paper-versus-backtest and paper-versus-live comparisons
- Shadow mode (paper deployment mirroring a live deployment)

### Actors

| Actor | Interaction |
|---|---|
| Quantitative Trader / Researcher | Runs paper deployments and manual paper orders |
| Trading Engine | Manages paper deployments with the same lifecycle as live |
| Execution simulator | Simulates fills |

## 26.3 Domain Entities

### 26.3.1 PaperAccount

A TradingAccount (19.3.1) with `account_mode = PAPER` and `venue_type = SIMULATED`, plus:

| Attribute | Type | Constraints |
|---|---|---|
| simulated_venue_id | Reference → Venue | Venue whose market data and rules are simulated |
| initial_balances | List of Money | Required |
| fee_schedule_id | Reference | Default: venue's standard retail tier |
| fill_model / slippage_model / latency_model | As in 25.3.1 | Defaults per workspace |
| margin_model | As in 25.3.1 | Default by venue type |
| reset_count | Integer | System-managed |

## 26.4 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-26001 | The system shall allow users to create paper accounts simulating any connected venue, with user-defined initial balances. | M | T |
| FR-26002 | The system shall allow paper accounts to be created without venue credentials when public market data is available for the venue. | M | T |
| FR-26003 | The system shall execute paper orders using the execution simulator against real-time market data, applying the account's fill, slippage, latency, fee, and margin models. | M | T |
| FR-26004 | The system shall apply the same OMS validation, RMS pre-trade checks, and Trading Engine eligibility rules to paper orders as to live orders. | M | T |
| FR-26005 | The system shall simulate funding payments for perpetual positions using real-time funding rates and borrow interest for margin positions. | M | T |
| FR-26006 | The system shall simulate liquidation when a paper margin account breaches maintenance margin, using the simulated venue's liquidation rules. | S | T |
| FR-26007 | The system shall maintain paper positions, balances, and P&L with the same Position and Portfolio Engine logic as live accounts. | M | T |
| FR-26008 | The system shall visually distinguish paper accounts, orders, and positions from live ones in every user interface view (for example a persistent "PAPER" badge and distinct color). | M | D |
| FR-26009 | The system shall allow a paper account to be reset to its initial or new balances; reset shall close all positions, cancel all orders, archive history under the prior reset number, and require confirmation. | M | T |
| FR-26010 | The system shall allow adding or removing virtual funds, recorded as capital flows. | M | T |
| FR-26011 | The system shall support shadow mode: a paper deployment configured to mirror a live deployment's strategy version and parameters, used to measure live-versus-simulation divergence. | S | T |
| FR-26012 | The system shall produce a paper-versus-live divergence report for shadow pairs covering fill rate, fill price difference, slippage, and P&L difference. | S | T |
| FR-26013 | The system shall produce the paper-versus-backtest comparison report required for promotion (FR-24045) by running a backtest over the paper period with the same configuration. | M | T |
| FR-26014 | The system shall continue paper trading during venue trading-connection outages as long as market data is available, since no venue order connection is required. | M | T |
| FR-26015 | The system shall mark paper fills with `is_simulated = true` and exclude them from all live P&L, tax-support, and compliance reports. | M | T |

## 26.5 Business Rules

| ID | Rule |
|---|---|
| BR-26-01 | A paper account can never be converted to a live account (FR-19004). |
| BR-26-02 | A paper deployment and a live deployment may trade the same instrument simultaneously; they never share positions. |
| BR-26-03 | Paper simulation of a limit order that would rest uses the real order book for queue estimation but never affects the real market. |

## 26.6 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-26001 | Given a paper account with 10,000 USDT and a risk limit of 1,000 USDT per order, when a paper order of 2,000 USDT notional is submitted, then it is rejected by RMS exactly as a live order would be. | FR-26004 |
| AC-26002 | Given a paper reset, then balances equal the configured values, no open orders or positions remain, and prior history is retrievable under the previous reset number. | FR-26009 |
| AC-26003 | Given a live P&L report, then no paper fills are included. | FR-26015 |

---

*End of Chapter 26 – Paper Trading*
