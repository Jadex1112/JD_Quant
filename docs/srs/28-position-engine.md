# Chapter 28 – Position Engine

## 28.1 Purpose

The Position Engine maintains the authoritative internal record of positions per account, instrument, position side, and attributed deployment. It applies fills to positions, computes cost basis, realized and unrealized P&L, reconciles positions with venues, and supplies position state to the RMS, Portfolio Engine, strategies, and user interfaces.

## 28.2 Domain Entities

### 28.2.1 Position

| Attribute | Type | Constraints |
|---|---|---|
| account_id | Reference | Required |
| instrument_id | InstrumentId | Required |
| deployment_id | Reference | Required (attribution, BR-19-03) |
| position_side | Enum: NET, LONG, SHORT | Hedge-mode venues use LONG/SHORT |
| quantity | Decimal | Signed for NET (positive long, negative short) |
| average_entry_price | Price | Per cost basis method |
| cost_basis | Money | Total cost of open quantity |
| realized_pnl | Money | Cumulative for position lifetime |
| unrealized_pnl | Money | Mark-to-market |
| fees_paid | Money | Cumulative |
| funding_paid | Money | Cumulative (perpetuals) |
| margin_used | Money | Initial margin (derivatives) |
| leverage | Decimal | Derivatives |
| liquidation_price | Price | Derivatives (FR-27046) |
| opened_at / last_fill_at | Timestamp | Lifecycle |
| status | Enum: OPEN, CLOSED | CLOSED when quantity = 0 |

### 28.2.2 Lot (tax lot)

| Attribute | Description |
|---|---|
| position_id | Parent |
| open_fill_id | Opening fill |
| open_quantity / remaining_quantity | Quantities |
| open_price | Price |
| opened_at | Timestamp |

### 28.2.3 PositionReconciliation

| Attribute | Description |
|---|---|
| account_id, instrument_id | Identity |
| internal_quantity / venue_quantity | Values compared |
| difference | venue − internal |
| resolution | NONE, ADJUSTED, ESCALATED |

## 28.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-28001 | The system shall update the position for (account, instrument, position side, deployment) on every accepted fill atomically with fill processing. | M | T |
| FR-28002 | The system shall support cost basis methods FIFO (default), LIFO, AVERAGE, and HIFO, configurable per account; changing the method applies to future closing fills only. | M (FIFO, AVERAGE), S (others) | T |
| FR-28003 | The system shall maintain tax lots for spot positions and consume them on closing fills according to the cost basis method. | M | T |
| FR-28004 | The system shall compute realized P&L on each closing fill as described in BR-28-01 and BR-28-02. | M | T |
| FR-28005 | The system shall compute unrealized P&L continuously using the reference price rule (BR-20-04). | M | T |
| FR-28006 | The system shall handle position flips (a fill that closes and reverses a position) by splitting the fill into a closing part and an opening part. | M | T |
| FR-28007 | The system shall apply funding payments to perpetual positions from venue funding events and attribute them to the deployment holding the position at the funding timestamp. | M | T |
| FR-28008 | The system shall apply fees in quote or base asset; fees paid in a third asset shall be converted to quote currency at the fill time for P&L. | M | T |
| FR-28009 | The system shall reconcile net positions per (account, instrument, side) with venue-reported positions on connection, at a configurable interval (default 60 s), and after every reconnect. | M | T |
| FR-28010 | The system shall, on reconciliation mismatch, adjust the internal net position to the venue value (CON-042) by creating an ADJUSTMENT position entry attributed to the MANUAL pseudo-deployment, raise a High alert, and record the reconciliation. | M | T |
| FR-28011 | The system shall provide the position view per account, deployment, and aggregated per instrument across deployments. | M | T |
| FR-28012 | The system shall provide position history (every change with the causing fill or adjustment). | M | T |
| FR-28013 | The system shall provide the RMS with position updates within 1 ms p99 of fill processing. | M | T |
| FR-28014 | The system shall allow an authorized user to transfer attribution of a position (or part of it) between deployments on the same account without creating venue orders; the transfer shall be audited and occur at the current reference price for P&L purposes. | S | T |
| FR-28015 | The system shall apply corporate actions (splits, symbol changes) to open equity positions and lots. | S | T |
| FR-28016 | The system shall close derivatives positions at contract expiry using the venue settlement price. | M | T |

## 28.4 Business Rules

| ID | Rule |
|---|---|
| BR-28-01 | Linear instruments: realized P&L = closed quantity × (exit price − entry price) × side_sign × contract_multiplier − attributable fees. |
| BR-28-02 | Inverse contracts (settled in base asset): realized P&L = closed quantity × contract_multiplier × (1/entry price − 1/exit price) × side_sign. |
| BR-28-03 | Unrealized P&L = quantity × (reference price − average entry price) × contract_multiplier for linear instruments. |
| BR-28-04 | The sum of positions across deployments on an account equals the account's net venue position after reconciliation. |
| BR-28-05 | Under AVERAGE cost basis, opening fills update average entry price as the quantity-weighted average; closing fills do not change it. |

## 28.5 Events

| Event | Description |
|---|---|
| `position.updated` | Position change with causing fill |
| `position.opened` / `position.closed` | Lifecycle |
| `position.reconciliation.mismatch` | Mismatch detected and resolution |
| `position.funding.applied` | Funding payment applied |

## 28.6 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-28001 | Given buys of 1 @ 100 and 1 @ 110 then a sell of 1 @ 120 under FIFO, then realized P&L = 20 (before fees) and remaining entry price = 110. | FR-28002, BR-28-01 |
| AC-28002 | Given a long of 2 and a sell fill of 5, then realized P&L is computed on 2 and a short position of 3 is opened at the fill price. | FR-28006 |
| AC-28003 | Given an internal position of 5 and venue position of 4, then after reconciliation the net internal position is 4 with an ADJUSTMENT of −1 and a High alert. | FR-28010 |

---

*End of Chapter 28 – Position Engine*
