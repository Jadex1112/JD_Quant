# Part C – Functional Requirements: Introduction and Conventions

## C.1 Purpose

Part C specifies the functional requirements of every JD Quant AI subsystem. Each chapter corresponds to one functional module listed in the Table of Contents and to one or more logical engines defined in Chapter 4.12.

## C.2 Chapter Structure

Every Part C chapter follows the same structure so that requirements can be located predictably:

| Section | Content |
|---|---|
| N.1 Purpose | Responsibility of the module |
| N.2 Scope and Actors | In-scope functions, excluded functions, interacting users and engines |
| N.3 Domain Entities | Business entities owned by the module and their attributes |
| N.4 State Model | Lifecycle states and transitions (where applicable) |
| N.5+ Functional Requirements | Uniquely identified requirements grouped by feature |
| Business Rules | Invariants and calculation rules |
| Events | Domain events published and consumed |
| Configuration | Configurable parameters with defaults |
| Error Conditions | Error codes and required handling |
| Acceptance Criteria | Testable criteria traced to requirements |

Sections not applicable to a module are omitted.

## C.3 Requirement Identifier Scheme

Requirement identifiers follow Chapter 1.9. Within Part C the five-digit number encodes the chapter:

```text
FR-CCNNN
   │ └── sequence within chapter (001–999)
   └──── chapter number (19–63)
```

Example: `FR-21014` is the fourteenth functional requirement of Chapter 21 – Order Management. AI-specific requirements use `AI-CCNNN`, security-specific requirements use `SEC-CCNNN`, and operations requirements use `OPS-CCNNN` with the same encoding. Business rules use `BR-CC-NN`, acceptance criteria use `AC-CCNNN`, and error codes use upper snake case (for example `ORDER_NOT_FOUND`).

## C.4 Requirement Attributes

Each requirement table contains:

| Column | Meaning |
|---|---|
| ID | Unique, stable identifier |
| Requirement | Normative statement using "shall" |
| Pri | Priority: **M** = Must (Release 1.0), **S** = Should (Release 1.x), **C** = Could (future release) |
| Ver | Verification method: **T** = Test, **I** = Inspection, **A** = Analysis, **D** = Demonstration |

## C.5 Normative Language

| Keyword | Meaning |
|---|---|
| shall | Mandatory requirement |
| shall not | Mandatory prohibition |
| should | Recommended; deviation requires documented justification |
| may | Optional |

## C.6 Common Entity Attributes

Unless stated otherwise, every persistent entity defined in Part C carries the following attributes, which are not repeated in each chapter:

| Attribute | Type | Description |
|---|---|---|
| id | UUID | System-generated immutable identifier |
| created_at | Timestamp (UTC, μs) | Creation time |
| created_by | Principal reference | User or service account that created the entity |
| updated_at | Timestamp (UTC, μs) | Last modification time |
| updated_by | Principal reference | Last modifier |
| version | Integer | Optimistic concurrency version, incremented on every update |
| workspace_id | UUID | Owning workspace (tenant isolation boundary) |

## C.7 Common Data Types

| Type | Definition |
|---|---|
| Decimal | Fixed-point decimal with up to 38 significant digits and 18 fractional digits (CON-022) |
| Price | Decimal constrained to the instrument tick size |
| Quantity | Decimal constrained to the instrument lot size |
| Money | Decimal amount plus ISO 4217 or asset currency code |
| Timestamp | UTC instant with microsecond resolution (CON-023) |
| InstrumentId | Canonical internal instrument identifier (CON-123) |
| Side | BUY or SELL |
| Enum | Closed set of upper-snake-case values listed in the owning chapter |

## C.8 Common Error Handling

All functional operations shall return, on failure, a structured error containing: error code, human-readable message, correlation identifier, timestamp, and (where applicable) the field or entity that caused the error. Error codes are stable and documented per chapter.

## C.9 Chapter Index

| Chapter | Module | Primary Engine(s) |
|---|---|---|
| 19 | Trading Engine | LTE |
| 20 | Market Data Engine | MDE, DIL, DPL |
| 21 | Order Management | OMS |
| 22 | Execution Engine | EMS |
| 23 | Portfolio Engine | PME |
| 24 | Strategy Framework | SE |
| 25 | Backtesting | BTE |
| 26 | Paper Trading | PTE |
| 27 | Risk Engine | RMS |
| 28 | Position Engine | PME |
| 29 | Inventory Engine | PME |
| 30 | Signal Engine | SE |
| 31 | Analytics | AAE |
| 32 | Performance Measurement | AAE |
| 33 | Notification | NCE |
| 34 | Scheduling | WFE |
| 35 | Task Engine | WFE |
| 36 | Logging | LME |
| 37 | Reporting | AAE |
| 38 | Audit | LME, SAE |
| 39 | Permissions | SAE |
| 40 | User Management | SAE |
| 41 | Settings | CCE |
| 42 | Configuration | CCE |
| 43 | Data Import | DIL |
| 44 | Data Export | DPL |
| 45 | Exchange Connectivity | EMS, MDE |
| 46 | Broker Connectivity | EMS |
| 47 | Plugin System | PIE |
| 48 | Automation | WFE |
| 49 | Workflow Engine | WFE |
| 50 | Scenario Simulator | STE, RMS |
| 51 | Recovery | HME, all |
| 52 | AI Copilot | ARE |
| 53 | Prompt Engine | ARE |
| 54 | Research Assistant | ARE |
| 55 | Portfolio Optimizer | AOE |
| 56 | Execution Optimizer | AOE |
| 57 | Model Manager | MME |
| 58 | Training Pipeline | TPE |
| 59 | Inference Pipeline | IPE |
| 60 | Monitoring | LME, HME |
| 61 | Diagnostics | HME |
| 62 | Health Engine | HME |
| 63 | Feature Store | FSE |
