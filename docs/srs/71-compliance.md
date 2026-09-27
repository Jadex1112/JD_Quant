# Chapter 71 – Compliance

## 71.1 Purpose

This chapter specifies requirements that enable organizations using JD Quant AI to meet their regulatory, contractual, and internal governance obligations. JD Quant AI is not itself a regulated entity (Chapter 2.4, CON-060); it provides controls and evidence.

## 71.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-71001 | The system shall retain order, execution, and audit records for the configured retention period (default ≥ 7 years, CON-062) in a form that is complete, accurate, and retrievable within 24 hours for any date range. | M | T |
| NFR-71002 | The system shall produce records sufficient to reconstruct any order's lifecycle with timestamps at microsecond precision (FR-38007). | M | T |
| NFR-71003 | The system shall support configurable jurisdiction restrictions on instruments, venues, and features (CON-065). | M | T |
| NFR-71004 | The system shall support data subject requests (access, rectification, erasure where permitted) for personal data within 30 days (CON-063). | M | T |
| NFR-71005 | The system shall document where personal data is stored and processed and support data residency configuration for storage location. | S | I |
| NFR-71006 | The system shall enforce market data license entitlements for display, storage, and export (CON-066). | M | T |
| NFR-71007 | The system shall provide pre-trade controls commonly required for algorithmic trading: price collars, maximum order size/value, message rate limits, kill functionality, and restricted lists (Chapter 27). | M | T |
| NFR-71008 | The system shall provide evidence packages for control testing: configuration history of risk limits, approvals, access reviews, and kill switch tests. | M | T |
| NFR-71009 | The system shall label AI-generated content and provide model documentation (model cards) for models used in trading decisions (CON-143, AI-57004). | M | I |
| NFR-71010 | The system shall support periodic kill switch testing in paper/sandbox mode with recorded results. | S | T |
| NFR-71011 | The system shall support legal hold on records, suspending retention-based deletion for specified scopes. | S | T |

---

*End of Chapter 71 – Compliance*
