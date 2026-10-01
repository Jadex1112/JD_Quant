# Chapter 74 – Internationalization

## 74.1 Purpose

This chapter specifies internationalization (i18n) and localization (l10n) requirements supporting users across regions, languages, currencies, and time zones.

## 74.2 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| NFR-74001 | All user-facing text shall be externalized into resource bundles; no user-facing string shall be hard-coded. | M | I |
| NFR-74002 | The initial release shall ship English (en); the architecture shall support adding languages without code changes. | M | I |
| NFR-74003 | Additional languages (for example Hindi, Simplified Chinese, Japanese, Spanish) shall be addable via resource bundles. | C | D |
| NFR-74004 | Dates, times, numbers, and currencies shall be formatted per the user's locale; timestamps shall be displayed in the user's timezone with UTC available on hover (CON-023). | M | T |
| NFR-74005 | Number formatting shall support Indian digit grouping (lakh/crore) and Western grouping per locale. | S | T |
| NFR-74006 | Prices and quantities shall be displayed with instrument precision regardless of locale formatting (CON-124). | M | T |
| NFR-74007 | The platform shall support any ISO 4217 currency and crypto asset codes as reporting currencies (Chapter 4.13 includes USD, INR, BTC, ETH). | M | T |
| NFR-74008 | The UI layout shall accommodate text expansion of up to 40% and support right-to-left scripts in the architecture. | S | I |
| NFR-74009 | Reports and notifications shall be generated in the recipient's locale where templates exist (FR-37014, FR-33014). | S | T |
| NFR-74010 | Trading calendars shall support venue-local time zones and regional holidays (FR-19030). | M | T |

---

*End of Chapter 74 – Internationalization*
