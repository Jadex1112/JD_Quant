# Chapter 41 – Settings

## 41.1 Purpose

The Settings module provides user-facing management of preferences and administrative options at user, workspace, and platform levels. Settings are the human-editable subset of configuration (Chapter 42) exposed through the user interface with validation, help text, and permission control.

## 41.2 Setting Levels and Precedence

| Level | Editable By | Examples |
|---|---|---|
| Platform | System Administrator | Allowed authentication methods, global rate limits, maintenance mode, email domain allowlist |
| Workspace | Workspace administrators | Default currency, promotion criteria, default risk profile, retention overrides, notification defaults |
| User | Each user | Theme, locale, timezone, chart defaults, notification preferences, default layouts |

Precedence: a lower level overrides a higher level only where the higher level marks the setting as overridable. Platform-level "locked" settings cannot be overridden.

## 41.3 Setting Definition

| Attribute | Description |
|---|---|
| key | Dot-notation key (4.14) |
| type | BOOLEAN, INTEGER, DECIMAL, STRING, ENUM, DURATION, LIST, JSON |
| default | Default value |
| constraints | Range, pattern, allowed values |
| level | PLATFORM, WORKSPACE, USER |
| overridable | Boolean |
| sensitive | Boolean (masked in UI and audit) |
| requires_restart | Boolean |
| category | Grouping for UI |
| description | Help text |

## 41.4 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-41001 | The system shall provide a settings interface grouped by category with search, showing current value, default, source level, and description for each setting. | M | D |
| FR-41002 | The system shall validate setting values against type and constraints before saving and display field-level errors. | M | T |
| FR-41003 | The system shall apply settings changes without restart unless the setting is marked requires_restart, in which case the UI shall indicate pending restart. | M | T |
| FR-41004 | The system shall resolve effective values according to 41.2 precedence and display the effective value and its source. | M | T |
| FR-41005 | The system shall allow reset of any setting to its inherited value. | M | T |
| FR-41006 | The system shall audit every settings change with before and after values (masked if sensitive). | M | T |
| FR-41007 | The system shall support export and import of workspace settings as a versioned document, excluding sensitive values. | S | T |
| FR-41008 | The system shall present a confirmation dialog describing impact for settings designated high-impact (for example retention periods, authentication policies, promotion criteria). | M | D |

## 41.5 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-41001 | Given a platform-locked setting, when a workspace administrator attempts to override it, then the change is rejected. | FR-41004 |
| AC-41002 | Given a user timezone change, then all timestamps in the UI render in the new timezone without re-login. | FR-41003 |

---

*End of Chapter 41 – Settings*
