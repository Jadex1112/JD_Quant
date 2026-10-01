# Chapter 73 – Accessibility

## 73.1 Purpose

This chapter specifies accessibility requirements so that JD Quant AI is usable by people with a wide range of abilities, aligned with WCAG 2.2 Level AA.

## 73.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-73001 | The web user interface shall conform to WCAG 2.2 Level AA. | M | T |
| NFR-73002 | All functionality shall be operable by keyboard alone with visible focus indicators and logical focus order. | M | T |
| NFR-73003 | Text and interactive elements shall meet contrast ratios of 4.5:1 (normal text) and 3:1 (large text, UI components, chart elements conveying information). | M | T |
| NFR-73004 | Information shall not be conveyed by color alone; P&L gains/losses, risk states, and order sides shall also use signs, icons, or text. | M | I |
| NFR-73005 | Charts shall provide accessible alternatives: data table view and textual summary of key values. | S | D |
| NFR-73006 | All controls shall have accessible names and roles for screen readers; live regions shall announce critical alerts (kill switch, risk breach) without announcing high-frequency data updates. | M | T |
| NFR-73007 | The interface shall support browser zoom to 200% without loss of content or functionality and reflow at 320 CSS px width for non-trading-grid views. | M | T |
| NFR-73008 | Users shall be able to reduce motion and pause auto-updating content (e.g. freeze a price grid). | S | T |
| NFR-73009 | Time limits (e.g. session timeout warnings, confirmation dialogs) shall provide warning and extension options, except where limited for security. | M | T |
| NFR-73010 | Accessibility shall be verified by automated checks in CI and manual audit with assistive technologies before each major release. | M | I |

---

*End of Chapter 73 – Accessibility*
