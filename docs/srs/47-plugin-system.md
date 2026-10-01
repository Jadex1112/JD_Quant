# Chapter 47 – Plugin System

## 47.1 Purpose

The Plugin Integration Engine (PIE) allows JD Quant AI to be extended without modifying core platform code (Chapter 10.10). Plugins can add venue adapters, data sources, indicators, strategy templates, risk checks, notification channels, report sections, analytics widgets, and AI tools, executed within an isolated and permission-controlled context (CON-013).

## 47.2 Plugin Types

| Type | Extension Point |
|---|---|
| VENUE_ADAPTER | Exchange/broker adapter interface (45.2) |
| DATA_SOURCE | Import source or streaming data source |
| INDICATOR | Indicator library entry |
| STRATEGY_TEMPLATE | Template library entry |
| RISK_CHECK | Additional pre-trade or post-trade check |
| NOTIFICATION_CHANNEL | Channel implementation |
| REPORT_SECTION | Report template section |
| DASHBOARD_WIDGET | Analytics widget |
| AI_TOOL | Tool callable by the AI Copilot |
| WORKFLOW_ACTION | Action usable in workflows |

## 47.3 Plugin Manifest

| Field | Description |
|---|---|
| id, name, version | Identity (semantic version) |
| type | From 47.2 |
| publisher | Publisher identity; signature |
| platform_compatibility | Supported platform version range |
| permissions | Declared permissions (e.g. `network:api.example.com`, `marketdata:read`, `orders:none`) |
| resources | CPU, memory limits |
| configuration_schema | Plugin settings schema |
| entrypoints | Implementations per extension point |
| checksum | Artifact hash |

## 47.4 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-47001 | The system shall support installation of plugins of the types in 47.2 from an uploaded package or configured registry. | M (VENUE_ADAPTER, INDICATOR, DATA_SOURCE), S (others) | T |
| FR-47002 | The system shall validate the manifest, signature, checksum, and platform compatibility before installation; unsigned plugins shall be installable only when the administrator enables "allow unsigned" for non-production environments. | M | T |
| FR-47003 | The system shall display declared permissions to the administrator and require explicit approval before enabling a plugin. | M | T |
| FR-47004 | The system shall execute plugins in an isolated context enforcing declared permissions: network access restricted to declared hosts, no file system access outside a plugin sandbox, no access to secrets other than those explicitly bound to the plugin configuration. | M | T |
| FR-47005 | The system shall enforce plugin resource limits and disable a plugin that repeatedly violates them, raising an alert. | M | T |
| FR-47006 | The system shall prevent RISK_CHECK plugins from loosening core risk decisions: a plugin can only add rejections, never approve an order rejected by core checks. | M | T |
| FR-47007 | The system shall support enabling, disabling, upgrading, and uninstalling plugins; upgrades that change permissions require re-approval. | M | T |
| FR-47008 | The system shall version plugin configuration and retain history. | M | T |
| FR-47009 | The system shall provide a plugin SDK with documentation, interface definitions, a local test harness, and the conformance suites relevant to each type. | M | I |
| FR-47010 | The system shall isolate plugin failures so that an exception in a plugin does not crash the host engine; failures are logged and counted. | M | T |
| FR-47011 | The system shall audit plugin installation, permission approval, enable/disable, and uninstallation. | M | T |
| FR-47012 | The system shall support a curated plugin marketplace listing (Chapter 2.11 future scope). | C | D |

## 47.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-47001 | Given a plugin attempting to reach a host not declared in its manifest, then the connection is blocked and logged. | FR-47004 |
| AC-47002 | Given a RISK_CHECK plugin returning APPROVE for an order rejected by core checks, then the order remains rejected. | FR-47006 |
| AC-47003 | Given a plugin throwing exceptions on every call, then the host engine continues operating and the plugin is disabled after the configured threshold. | FR-47005, FR-47010 |

---

*End of Chapter 47 – Plugin System*
