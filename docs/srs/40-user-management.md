# Chapter 40 – User Management

## 40.1 Purpose

The User Management module, part of the SAE, manages identities (users, service accounts, API clients), authentication, sessions, workspaces, and the user lifecycle defined in Chapter 13.3.

## 40.2 Domain Entities

### 40.2.1 User

| Attribute | Type | Constraints |
|---|---|---|
| email | String(254) | Unique, verified |
| display_name | String(100) | Required |
| status | Enum: INVITED, ACTIVE, SUSPENDED, DEACTIVATED, ARCHIVED | Lifecycle |
| auth_source | Enum: LOCAL, SSO | Required |
| mfa_enrolled | Boolean | Required for privileged roles |
| mfa_methods | List: TOTP, WEBAUTHN, SMS (discouraged), RECOVERY_CODES | |
| locale / timezone | Locale / IANA | Defaults from workspace |
| last_login_at | Timestamp | |
| failed_login_count | Integer | Reset on success |
| locked_until | Timestamp | Lockout |

### 40.2.2 Workspace

| Attribute | Description |
|---|---|
| name | Unique name |
| members | Users with role bindings |
| settings | Workspace-level settings (Chapter 41) |
| deployment_mode | SINGLE_USER or MULTI_USER (affects CON-203 waiver) |

### 40.2.3 ServiceAccount and ApiKey

| Attribute | Description |
|---|---|
| name, owner | Identity and responsible human owner |
| credentials | API key (prefix + hashed secret), OAuth client credentials, or mTLS certificate |
| scopes | Permission subset (FR-39015) |
| ip_allowlist | Optional CIDR list |
| expires_at | Required; maximum 1 year |
| last_used_at | Tracking |

### 40.2.4 Session

| Attribute | Description |
|---|---|
| session_id | Opaque identifier |
| user_id, created_at, last_activity_at, expires_at | Lifecycle |
| ip_address, user_agent, device | Context |
| mfa_verified_at | For step-up (FR-39008) |

## 40.3 Functional Requirements – Identity Lifecycle

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-40001 | The system shall allow administrators to invite users by email; invitations expire after a configurable period (default 7 days). | M | T |
| FR-40002 | The system shall support self-registration only when enabled by the System Administrator, with email verification and admin approval before access. | S | T |
| FR-40003 | The system shall support the lifecycle states in 40.2.1 and record every transition in the audit trail (13.3). | M | T |
| FR-40004 | The system shall, on suspension or deactivation, revoke all sessions and API keys of the user within 5 s and pause any automations whose policy requires the user's supervision. | M | T |
| FR-40005 | The system shall prevent deletion of users referenced by retained trading or audit records; such users shall be archived and personal data anonymized on request where permitted (CON-063). | M | T |
| FR-40006 | The system shall provide the first-run bootstrap: creation of the initial System Administrator account with mandatory MFA enrollment. | M | T |
| FR-40007 | The system shall support SCIM 2.0 provisioning from enterprise identity providers. | C | T |

## 40.4 Functional Requirements – Authentication

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-40020 | The system shall support local authentication with passwords meeting a configurable policy (default minimum 12 characters, breached-password check, no forced periodic rotation). | M | T |
| FR-40021 | The system shall store passwords using a memory-hard adaptive hash (Argon2id or equivalent approved algorithm). | M | I |
| FR-40022 | The system shall support MFA using TOTP and WebAuthn/passkeys, with one-time recovery codes. | M | T |
| FR-40023 | The system shall require MFA for any user holding a role with privileged permissions (FR-39008), and allow administrators to require MFA for all users. | M | T |
| FR-40024 | The system shall support SSO via OpenID Connect and SAML 2.0 with role mapping from identity provider groups (CON-183). | S | T |
| FR-40025 | The system shall lock an account after a configurable number of consecutive failed logins (default 5) for an increasing duration (default 15 min, doubling) and notify the user. | M | T |
| FR-40026 | The system shall return identical responses for unknown user and wrong password to prevent account enumeration. | M | T |
| FR-40027 | The system shall provide secure password reset via single-use, time-limited (default 30 min) tokens delivered by email; reset shall revoke existing sessions. | M | T |
| FR-40028 | The system shall provide a break-glass local administrator login usable when SSO is unavailable (DEP-006), protected by MFA and generating a Critical alert when used. | M | T |

## 40.5 Functional Requirements – Sessions and API Keys

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-40040 | The system shall expire sessions after configurable idle (default 30 min) and absolute (default 12 h) timeouts. | M | T |
| FR-40041 | The system shall allow users to view and revoke their active sessions and administrators to revoke any session. | M | T |
| FR-40042 | The system shall limit concurrent sessions per user (default 5), revoking the oldest when exceeded. | S | T |
| FR-40043 | The system shall allow users to create API keys scoped to a subset of their permissions (FR-39015), displaying the secret exactly once at creation. | M | T |
| FR-40044 | The system shall store only a hash of API key secrets. | M | I |
| FR-40045 | The system shall notify key owners 14 days before API key expiry and disable keys at expiry. | M | T |
| FR-40046 | The system shall detect and alert on anomalous logins: new device, new country, impossible travel. | S | T |

## 40.6 Functional Requirements – Preferences and Profile

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| FR-40060 | The system shall allow users to manage profile, locale, timezone, theme, notification preferences, default workspace, and saved layouts. | M | T |
| FR-40061 | The system shall allow users to belong to multiple workspaces and switch between them; data never crosses workspace boundaries without explicit sharing. | M | T |

## 40.7 Acceptance Criteria

| ID | Criterion | Traces To |
|---|---|---|
| AC-40001 | Given a suspended user, then any API call with their existing session or API keys is rejected within 5 s of suspension. | FR-40004 |
| AC-40002 | Given 5 failed logins, then the account is locked and further correct passwords are rejected until the lockout expires. | FR-40025 |
| AC-40003 | Given an API key created, then the secret cannot be retrieved again from any interface. | FR-40043, FR-40044 |

---

*End of Chapter 40 – User Management*
