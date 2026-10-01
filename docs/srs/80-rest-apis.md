# Chapter 80 – REST APIs

## 80.1 Purpose

This chapter specifies the public REST API through which the user interface, CLI, and external API clients interact with JD Quant AI (CON-014). Identifier format `API-80NNN`. Full schemas are published as an OpenAPI specification (CON-025) in Volume 7.

## 80.2 Conventions

| Aspect | Convention |
|---|---|
| Base path | `/api/v{major}` (e.g. `/api/v1`) |
| Resource naming | Plural kebab-case nouns (4.14), e.g. `/risk-profiles` |
| Identifiers | UUID path parameters |
| Format | JSON (UTF-8); decimals serialized as strings to preserve precision (CON-022) |
| Timestamps | ISO 8601 UTC with microseconds, e.g. `2026-01-15T09:30:00.123456Z` |
| Pagination | Cursor-based: `limit` (default 50, max 1000), `cursor`; responses include `next_cursor` |
| Filtering | Query parameters per field; time ranges `from`/`to` |
| Sorting | `sort=field,-other` |
| Idempotency | `Idempotency-Key` header on all POST that create resources (CON-008) |
| Concurrency | `If-Match` with entity version (ETag) on PUT/PATCH/DELETE |
| Errors | RFC 9457 problem details extended with `code`, `correlation_id`, `errors[]` (field-level) |
| Authentication | Bearer access token (user sessions), API key (`X-API-Key` + HMAC signature for trading endpoints), or mTLS |
| Rate limits | Headers: `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`; 429 on exceed |
| Correlation | `X-Correlation-Id` accepted and returned |
| Versioning | Major in path; additive changes within a major (CON-181, CON-182) |

## 80.3 HTTP Status Usage

| Status | Usage |
|---|---|
| 200 / 201 / 202 / 204 | OK / Created / Accepted (async job) / No content |
| 400 | Validation error |
| 401 | Unauthenticated |
| 403 | Forbidden (authorization) |
| 404 | Not found (also used for resources in other workspaces) |
| 409 | Conflict (state transition, version mismatch, duplicate) |
| 412 | Precondition failed (If-Match) |
| 422 | Business rule violation (e.g. RISK_REJECTED) |
| 429 | Rate limited |
| 503 | Service unavailable / SAFE state |

## 80.4 Resource Catalog (Release 1.0)

| Resource | Endpoints |
|---|---|
| Auth & Session | `POST /auth/login`, `POST /auth/mfa/verify`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me`, `GET /me/sessions`, `DELETE /me/sessions/{id}` |
| API Keys | `GET/POST /api-keys`, `DELETE /api-keys/{id}` |
| Users & Roles | `GET/POST /users`, `GET/PATCH /users/{id}`, `POST /users/{id}:suspend`, `GET/POST /roles`, `GET/POST/DELETE /role-bindings` |
| Workspaces | `GET/POST /workspaces`, `GET/PATCH /workspaces/{id}` |
| Venues & Instruments | `GET /venues`, `GET /instruments?query=&venue=&asset_class=`, `GET /instruments/{id}` |
| Market Data | `GET /market-data/candles`, `GET /market-data/trades`, `GET /market-data/order-book`, `GET /market-data/quotes/latest` |
| Datasets | `GET/POST /datasets`, `GET /datasets/{id}/versions`, `GET /datasets/{id}/coverage` |
| Connections | `GET/POST /connections`, `PATCH /connections/{id}`, `POST /connections/{id}:test`, `POST /connections/{id}:rotate-credentials` |
| Accounts | `GET/POST /accounts`, `GET/PATCH /accounts/{id}`, `POST /accounts/{id}:suspend`, `GET /accounts/{id}/balances`, `POST /paper-accounts/{id}:reset` |
| Orders | `POST /orders`, `GET /orders`, `GET /orders/{id}`, `PATCH /orders/{id}` (modify), `DELETE /orders/{id}` (cancel), `POST /orders:cancel-all`, `GET /orders/{id}/events`, `POST /orders:what-if` |
| Fills | `GET /fills`, `GET /fills/{id}` |
| Positions | `GET /positions`, `GET /positions/{id}/history`, `POST /positions:transfer` |
| Strategies | `GET/POST /strategies`, `GET/PATCH /strategies/{id}`, `GET/POST /strategies/{id}/versions`, `GET /strategies/{id}/versions/{v}`, `POST /strategies/{id}/versions/{v}:promote`, `GET /strategy-templates` |
| Deployments | `GET/POST /deployments`, `GET /deployments/{id}`, `POST /deployments/{id}:start|pause|resume|stop|flatten|retire|approve`, `GET /deployments/{id}/logs`, `GET /deployments/{id}/diagnostics` |
| Kill Switches | `GET /kill-switches`, `POST /kill-switches` (trigger), `POST /kill-switches/{id}:release` |
| Backtests | `POST /backtests`, `GET /backtests`, `GET /backtests/{id}`, `GET /backtests/{id}/results`, `GET /backtests/{id}/trades`, `POST /backtests/{id}:cancel`, `POST /backtests/{id}:rerun` |
| Optimizations | `POST /optimizations`, `GET /optimizations/{id}`, `GET /optimizations/{id}/trials` |
| Portfolios | `GET/POST /portfolios`, `GET /portfolios/{id}`, `GET /portfolios/{id}/snapshots`, `GET /portfolios/{id}/performance`, `GET /portfolios/{id}/exposure`, `POST /portfolios/{id}:optimize`, `GET/POST /rebalance-proposals`, `POST /rebalance-proposals/{id}:approve` |
| Risk | `GET/POST /risk-profiles`, `GET/PATCH /risk-profiles/{id}`, `POST /risk-profiles/{id}:activate`, `GET /risk/utilization`, `GET /risk/breaches`, `POST /risk/breaches/{id}:acknowledge`, `GET/POST /risk-exceptions`, `GET /risk/measures`, `POST /scenarios:run` |
| Signals | `GET/POST /signal-definitions`, `GET /signals`, `POST /signals/webhook/{definition_id}` |
| AI | `GET/POST /models`, `GET /models/{id}/versions`, `POST /models/{id}/versions/{v}:promote|rollback`, `POST /training-runs`, `GET /training-runs/{id}`, `GET/POST /features`, `POST /copilot/conversations`, `POST /copilot/conversations/{id}/messages` |
| Analytics & Reports | `GET /analytics/{view}`, `GET/POST /dashboards`, `POST /reports`, `GET /reports/{id}`, `GET/POST /report-schedules` |
| Operations | `GET /health`, `GET /status`, `GET /jobs`, `POST /jobs/{id}:cancel`, `GET/POST /schedules`, `GET/POST /automation-rules`, `GET/POST /workflows`, `GET /approvals`, `POST /approvals/{id}:approve|reject` |
| Notifications | `GET /notifications`, `POST /notifications/{id}:acknowledge`, `GET/POST /notification-rules` |
| Settings & Config | `GET/PATCH /settings`, `GET /config/effective` |
| Audit | `GET /audit-events`, `POST /audit-events:verify`, `POST /audit-events:export` |
| Plugins | `GET/POST /plugins`, `POST /plugins/{id}:enable|disable`, `DELETE /plugins/{id}` |
| Import/Export | `POST /imports`, `GET /imports/{id}`, `POST /exports`, `GET /exports/{id}` |

Custom actions use the `:{verb}` suffix convention.

## 80.5 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| API-80001 | The system shall implement the resource catalog of 80.4 following the conventions of 80.2 and 80.3. | M | T |
| API-80002 | The system shall publish a machine-readable OpenAPI specification generated from the implementation and validated in CI against contract tests. | M | T |
| API-80003 | The system shall require request signing (HMAC over method, path, body hash, timestamp, and nonce) for API-key access to trading endpoints, rejecting requests with timestamps outside ±30 s or reused nonces. | M | T |
| API-80004 | The system shall return 202 Accepted with a job reference for asynchronous operations (backtests, optimizations, reports, imports, exports, training). | M | T |
| API-80005 | The system shall enforce authorization on every endpoint (FR-39001) and return 404 for resources outside the principal's workspaces to avoid enumeration. | M | T |
| API-80006 | The system shall document every error code per endpoint. | M | I |
| API-80007 | The system shall provide official client SDKs generated from the OpenAPI specification for at least one language. | S | D |
| API-80008 | The system shall support webhook subscriptions for selected events (order state, fills, risk breaches, job completion), with signed payloads (FR-33012). | S | T |

---

*End of Chapter 80 – REST APIs*
