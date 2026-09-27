# Chapter 37 – Reporting

## 37.1 Purpose

The Reporting module produces structured documents from platform data for traders, portfolio managers, risk managers, executives, compliance officers, and auditors. Reports are generated on demand or on schedule, rendered in multiple formats, versioned, and distributed through the Notification engine.

## 37.2 Report Catalog

| Report | Content | Default Frequency | Pri |
|---|---|---|---|
| Daily Trading Summary | P&L by account/strategy, trades, fees, top movers, rejected orders | Daily | M |
| Portfolio Report | NAV, returns, holdings, allocation, exposure, benchmark comparison | Daily/Monthly | M |
| Risk Report | Limit utilization, breaches, VaR/CVaR, stress results, margin | Daily | M |
| Strategy Performance Report | Metrics (Ch. 32), equity curve, trade statistics, live vs expected | Weekly/Monthly | M |
| Execution Quality Report | Slippage, shortfall, fill rates, latency, venue comparison | Weekly | M |
| Backtest Report | Configuration, metrics, charts, assumptions (BR-25-04) | On demand | M |
| Monthly / Annual Performance Report | Period returns, attribution, fees, drawdowns | Monthly/Annually | M |
| Tax-Support Report | Realized gains/losses by lot, holding period classification, fees, funding income/expense | Annually/On demand | S |
| AI Model Report | Model performance, drift, feature importance, deployment history | Weekly | S |
| Operational Report | Uptime, incidents, connectivity, job statistics, data quality | Weekly | M |
| Audit Report | Filtered audit trail extract with integrity verification | On demand | M |
| Compliance Report | Access reviews, approvals, configuration changes, retention status | Monthly | M |
| User Activity Report | Logins, sessions, privileged actions | On demand | M |

The tax-support report assists users; it is not a tax filing (Chapter 2.9).

## 37.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-37001 | The system shall provide every report in the catalog of 37.2 with its stated priority. | M | T |
| FR-37002 | The system shall render reports in PDF, HTML, and XLSX, and provide underlying data in CSV and JSON. | M | T |
| FR-37003 | The system shall allow report parameters: period, portfolios, accounts, strategies, currency, and comparison benchmark. | M | T |
| FR-37004 | The system shall generate reports asynchronously as Task Engine jobs and notify the requester on completion. | M | T |
| FR-37005 | The system shall allow scheduling reports (Chapter 34) with distribution to users, roles, email addresses on an administrator-approved domain allowlist, or webhooks. | M | T |
| FR-37006 | The system shall generate each report from a consistent data snapshot so that all figures within a report correspond to the same as-of time. | M | T |
| FR-37007 | The system shall store generated reports immutably with parameters, generation time, data as-of time, template version, and content hash, retained per the configured policy. | M | T |
| FR-37008 | The system shall allow administrators to customize report templates (branding, sections, ordering) with template versioning. | S | T |
| FR-37009 | The system shall allow users to build custom reports from dashboard widgets (FR-31060). | S | D |
| FR-37010 | The system shall apply the requesting user's data permissions to report content; scheduled reports use the owner's permissions at generation time. | M | T |
| FR-37011 | The system shall label AI-generated narrative sections as AI-generated (CON-143). | M | I |
| FR-37012 | The system shall exclude simulated (paper, backtest) data from live reports unless explicitly requested and labeled. | M | T |
| FR-37013 | The system shall generate a standard daily report for a workspace with 50 accounts within 5 minutes. | M | T |
| FR-37014 | The system shall support report localization of labels, number formats, and date formats. | S | T |

## 37.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-37001 | Given a scheduled daily trading summary, then it is generated after the configured end-of-day time and delivered to all configured recipients with PDF and CSV attachments. | FR-37002, FR-37005 |
| AC-37002 | Given a user without access to account B, when they generate a portfolio report including all accounts, then account B data is excluded. | FR-37010 |
| AC-37003 | Given a stored report, then its content hash verifies unchanged content. | FR-37007 |

---

*End of Chapter 37 – Reporting*
