# Chapter 98 – Validation

## 98.1 Purpose

This chapter specifies how the platform is validated against user needs ("are we building the right product?").

## 98.2 Validation Activities

| Activity | Participants | Output |
|---|---|---|
| Persona walkthroughs | Representatives of each user class (Chapter 13) | Task success rates, issues |
| Usability testing | ≥ 5 users per primary persona | SUS scores (NFR-72012), findings |
| Paper-trading pilot | Pilot traders | Paper vs backtest consistency, workflow feedback |
| Controlled live pilot | Selected users with small capital and strict risk limits | Live vs paper divergence, operational incidents |
| Operational readiness review | Operations, Security, Risk | Readiness checklist sign-off |
| Compliance review | Compliance Officer | Evidence of controls (NFR-71008) |

## 98.3 Requirements

| ID | Requirement | Pri | Ver |
|---|---|---|---|
| OPS-98001 | Each user class shall validate its primary workflows (Chapter 13 typical activities) before general availability. | M | D |
| OPS-98002 | A paper-trading pilot of ≥ 4 weeks shall precede live availability, with divergence between paper and backtest results analyzed. | M | D |
| OPS-98003 | A controlled live pilot of ≥ 4 weeks with strict risk limits shall precede general live availability, with zero Critical correctness defects (duplicate orders, lost fills, position mismatches unresolved by reconciliation) as exit criteria. | M | D |
| OPS-98004 | Validation findings shall be triaged; findings indicating a requirement gap shall be handled through change control (Chapter 2.12). | M | I |

---

*End of Chapter 98 – Validation*
