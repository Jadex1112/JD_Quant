# Chapter 70 – Security

## 70.1 Purpose

This chapter consolidates the security requirements of JD Quant AI. Security requirements use the identifier format `SEC-70NNN`. Functional security capabilities (authentication, authorization, audit) are specified in Chapters 38–40; this chapter specifies cross-cutting security properties.

## 70.2 Threat Model Summary

| Asset | Primary Threats |
|---|---|
| Venue credentials | Theft, misuse to trade or withdraw |
| Trading accounts | Unauthorized orders, manipulation of strategies or limits |
| Strategies and models (IP) | Exfiltration |
| Audit trail | Tampering |
| User identities | Credential stuffing, phishing, session hijacking |
| Platform availability | Denial of service, resource exhaustion |
| AI features | Prompt injection, data leakage to external providers |
| Plugins | Malicious code execution, supply-chain compromise |

## 70.3 Requirements – Data Protection

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| SEC-70001 | All data at rest (databases, object storage, backups, logs) shall be encrypted with AES-256 or equivalent. | M | I |
| SEC-70002 | All network traffic shall use TLS 1.2+ (CON-081); internal service-to-service traffic in T2 deployments shall use mutual TLS. | M | T |
| SEC-70003 | Secrets shall be stored in a dedicated secret store with envelope encryption, access policies per component, and access logging (CON-080). | M | I |
| SEC-70004 | Encryption keys shall be rotated at least annually and upon suspected compromise, without downtime. | M | T |
| SEC-70005 | Venue credentials shall be decrypted only in the memory of the adapter process that uses them. | M | I |

## 70.4 Requirements – Application Security

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| SEC-70020 | The system shall be protected against OWASP Top 10 vulnerabilities; all input shall be validated at trust boundaries and output encoded for its context. | M | T |
| SEC-70021 | All state-changing web requests shall be protected against cross-site request forgery. | M | T |
| SEC-70022 | The web application shall set security headers: Content-Security-Policy, Strict-Transport-Security, X-Content-Type-Options, frame-ancestors restrictions. | M | T |
| SEC-70023 | Authentication tokens shall be short-lived (access ≤ 15 min) with refresh token rotation and reuse detection. | M | T |
| SEC-70024 | APIs shall apply per-principal and per-IP rate limiting and request size limits. | M | T |
| SEC-70025 | User strategy code and plugins shall run in sandboxes preventing access to the host, network (unless declared), secrets, and other tenants (CON-013, FR-24007, FR-47004). | M | T |
| SEC-70026 | Workspaces shall be isolated: no API shall return data from a workspace the principal is not a member of, verified by automated tenant-isolation tests. | M | T |
| SEC-70027 | AI features shall implement prompt-injection defenses (AI-52014) and data minimization (CON-144). | M | T |

## 70.5 Requirements – Supply Chain and Operations

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| SEC-70040 | Build artifacts shall be signed and verified at deployment; an SBOM shall be produced per release (CON-026). | M | I |
| SEC-70041 | Dependencies shall be scanned for known vulnerabilities in CI and continuously for deployed versions. | M | I |
| SEC-70042 | Container images shall run as non-root with read-only file systems where feasible and minimal base images. | M | I |
| SEC-70043 | Security events (failed logins, permission denials, secret access, integrity failures, anomalous logins) shall be forwarded to security monitoring within 1 minute. | M | T |
| SEC-70044 | Independent penetration testing shall be performed before the first production release and annually thereafter; critical findings shall be remediated before release. | M | I |
| SEC-70045 | A documented incident response plan shall define severity levels, roles, communication, and credential revocation procedures, exercised at least annually. | M | I |
| SEC-70046 | Administrative access to production infrastructure shall require MFA and be logged; standing privileged access shall be minimized through just-in-time access. | M | I |
| SEC-70047 | The system shall provide a one-action emergency revocation of all venue credentials for a workspace (disabling connections and triggering kill switches) for use during a suspected compromise. | M | T |

---

*End of Chapter 70 – Security*
