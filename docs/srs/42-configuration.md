# Chapter 42 – Configuration

## 42.1 Purpose

The Configuration & Control Engine (CCE) manages all externalized configuration of the platform (CON-012): component parameters, feature flags, environment-specific values, and references to secrets. It provides versioned, validated, auditable configuration with controlled rollout and runtime reload.

## 42.2 Configuration Sources and Precedence

From lowest to highest precedence:

1. Built-in defaults shipped with each component
2. Environment configuration files (per environment, Chapter 14.3)
3. Environment variables
4. Configuration store (runtime-managed, versioned)
5. Settings overrides (Chapter 41) for keys exposed as settings

Secrets are never stored as configuration values; configuration contains secret references resolved at runtime from the secret store (CON-080).

## 42.3 Functional Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-42001 | The system shall load configuration from the sources in 42.2 with the stated precedence. | M | T |
| FR-42002 | The system shall validate the complete configuration against a schema at startup and refuse to start with a clear error listing every invalid key if validation fails. | M | T |
| FR-42003 | The system shall version every change in the configuration store with author, timestamp, diff, and comment. | M | T |
| FR-42004 | The system shall propagate runtime configuration changes to all running instances within 10 s and notify subscribing components, which shall apply them without restart where the key is marked reloadable. | M | T |
| FR-42005 | The system shall support rollback to any previous configuration version. | M | T |
| FR-42006 | The system shall resolve secret references from the secret store at runtime and refresh them upon rotation without restart. | M | T |
| FR-42007 | The system shall provide feature flags with states ON, OFF, and PERCENTAGE / targeted (by workspace or user), evaluated consistently across instances. | M | T |
| FR-42008 | The system shall require approval for configuration changes to keys designated critical (risk, execution, security) in production (CON-202). | M | T |
| FR-42009 | The system shall expose the effective configuration of each running instance (with secrets masked) to Operations Engineers. | M | T |
| FR-42010 | The system shall detect configuration drift between instances of the same component and alert. | S | T |
| FR-42011 | The system shall support promotion of configuration versions between environments (development → testing → staging → production) with diff review. | S | T |
| FR-42012 | The system shall provide a schema registry of all configuration keys with type, default, description, reloadable flag, and owning component. | M | I |

## 42.4 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-42001 | Given an invalid value for a required key, then the component refuses to start and reports the key and violated constraint. | FR-42002 |
| AC-42002 | Given a reloadable key changed in the store, then all instances apply it within 10 s without restart. | FR-42004 |
| AC-42003 | Given a rotated venue secret, then subsequent venue requests use the new secret without restart. | FR-42006 |

---

*End of Chapter 42 – Configuration*
