# Chapter 90 – Sequence Behaviour

## 90.1 Purpose

This chapter specifies the normative sequences of interactions for the platform's key end-to-end flows. Implementations may parallelize steps only where noted.

## 90.2 SEQ-01 — Live Strategy Order (Happy Path)

```text
Venue ─md─▶ Adapter ─▶ MDE ─▶ SE(strategy.on_bar) ─intent─▶ OMS
OMS: validate ─▶ RMS: pre-trade decision ─▶ OMS: PENDING_SUBMIT ─▶ EMS
EMS: rate-limit ─▶ Adapter ─▶ Venue
Venue ─ack─▶ Adapter ─▶ EMS ─report─▶ OMS: OPEN ─event─▶ SE.on_order_update, UI
Venue ─fill─▶ Adapter ─▶ EMS ─report─▶ OMS: FILLED ─order.fill─▶ Position Engine
Position Engine ─position.updated─▶ RMS (state), PME (NAV), SE.on_fill, UI
```

1. MDE publishes the normalized record with receive_ts.
2. SE delivers it to the deployment; strategy submits an intent with signal reference.
3. OMS validates, assigns client order id, persists CREATED → PENDING_RISK.
4. RMS evaluates and returns a decision (persisted).
5. OMS persists PENDING_SUBMIT and hands to EMS.
6. EMS acquires rate-limit tokens, sends via adapter; OMS persists SUBMITTED.
7. Adapter receives ack → ExecutionReport ACK → OMS OPEN.
8. Fill → OMS applies fill (de-duplicated), Position Engine updates position atomically with fill processing (FR-28001), events published.

## 90.3 SEQ-02 — Order Timeout and Resolution

1. SUBMITTED order receives no ack within `oms.ack.timeout` → OMS sets UNKNOWN (FR-21046).
2. EMS queries venue by client order id per retry schedule (FR-22025).
3. If found → apply reported state and fills; if venue confirms not found → REJECTED (reason NOT_RECEIVED); strategy notified; strategy may submit a new intent.
4. If unresolved → remains UNKNOWN, Critical alert; the order counts as working for risk (BR-21-03).

## 90.4 SEQ-03 — Kill Switch Trigger

1. Principal or risk rule triggers kill switch (scope, action).
2. Trading Engine persists TRIGGERED and publishes `killswitch.triggered`.
3. OMS and RMS update eligibility caches (≤ 50 ms, FR-19041); subsequent intents in scope rejected.
4. For CANCEL_OPEN/FLATTEN: OMS issues cancel requests (prioritized by EMS, FR-22021).
5. For FLATTEN: Trading Engine issues closing orders (source SYSTEM) which pass RMS as reduce-only.
6. Deployments in scope → HALTED; NCE notifies roles (FR-19049).

## 90.5 SEQ-04 — Deployment Start (Warm-up)

As specified in FR-19014 steps 1–8, executed strictly in order; failure at any step transitions to FAILED with the step recorded.

## 90.6 SEQ-05 — Backtest Execution

1. User submits configuration → validation (FR-25022) → dataset version resolved or created.
2. Job queued in Task Engine → worker allocated.
3. Worker loads strategy artifact, dataset, simulator, and simulated clock.
4. Events replayed in deterministic order (FR-25002); strategy callbacks; simulated OMS/RMS/EMS.
5. Progress reported every ≤ 2 s.
6. Results persisted; metrics computed (Chapter 32); run COMPLETED; user notified.

## 90.7 SEQ-06 — Reconnect and Reconcile

1. Adapter detects disconnection → `connection.state.changed` (DISCONNECTED); EMS blocks new orders to venue.
2. Reconnect with backoff; re-authenticate; re-subscribe.
3. Reconcile open orders (FR-21083), fills (FR-21085), balances (FR-23010), positions (FR-28009) — in this order.
4. Publish reconciliation results; unblock order submission; deployments resume per configuration.

## 90.8 SEQ-07 — Model Promotion

As specified in the Model Promotion workflow (49.3): evaluation report → threshold check → approvals → SHADOW deployment → shadow observation period → approval → PRODUCTION (atomic switch, AI-59007) with rollback target recorded.

## 90.9 SEQ-08 — Copilot Action

1. User message → Prompt Engine renders template with context → LLM.
2. LLM proposes tool call → permission check against user (AI-52002).
3. READ_ONLY → executed; result returned to LLM as data.
4. STATE_CHANGING/TRADING → confirmation card to user (AI-52004); on confirm → executed as the user; audited (AI-52009).

## 90.10 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-90001 | Implementations shall follow sequences SEQ-01 to SEQ-08 in the specified order of persisted state changes. | M | T |
| FR-90002 | Each sequence shall have an automated end-to-end test in a simulated venue environment. | M | T |
| FR-90003 | Sequence steps that publish events shall do so only after the corresponding state change is durable (API-82009). | M | T |

---

*End of Chapter 90 – Sequence Behaviour*
