# Chapter 72 – Usability

## 72.1 Purpose

This chapter specifies usability requirements ensuring that each user class (Chapter 13) can accomplish its goals effectively, efficiently, and safely. Detailed visual design is specified in Volume 8.

## 72.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-72001 | A new trader shall be able to complete a paper trade from a template strategy within 15 minutes of first login without assistance (QAS-10), verified with ≥ 5 representative test users achieving ≥ 80% success. | M | T |
| NFR-72002 | The interface shall provide role-based default workspaces and navigation (13.22). | M | D |
| NFR-72003 | High-impact actions (live order submission above a configurable notional, flatten, kill switch release, limit loosening, deletion) shall require explicit confirmation summarizing the consequences. | M | D |
| NFR-72004 | The interface shall clearly distinguish LIVE and PAPER contexts at all times (FR-26008) using color, labels, and iconography that do not rely on color alone. | M | D |
| NFR-72005 | Every error message shall state what happened, why, and how to resolve it, and include the correlation identifier for support. | M | I |
| NFR-72006 | The interface shall support keyboard shortcuts for frequent trading actions, including a configurable shortcut for the kill switch panel. | S | D |
| NFR-72007 | The interface shall provide contextual help for every screen and definitions for every metric (FR-32055). | M | D |
| NFR-72008 | The interface shall support light and dark themes and user-configurable layouts saved per user (FR-40060). | M | D |
| NFR-72009 | Forms shall validate input inline and preserve user input on error. | M | T |
| NFR-72010 | Long-running operations shall show progress and allow the user to continue working (CON-102). | M | D |
| NFR-72011 | The interface shall be responsive for monitoring use on tablet-sized screens (≥ 768 px width); full trading and research functionality targets desktop (≥ 1280 px). | S | D |
| NFR-72012 | System Usability Scale (SUS) score measured with representative users shall be ≥ 75 before general availability. | S | T |

---

*End of Chapter 72 – Usability*
