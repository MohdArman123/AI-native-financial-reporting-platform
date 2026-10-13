# ERP financial statements - Manual Adjustments Agent (prototype slice)

Take-home slice: **validate manual journal entries, detect balance violations, explain rejections in plain
English**, run against the deliberately messy bundle in `data/` (untouched).

## Run
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install pytest            # only needed for tests; the pipeline itself is stdlib-only
PYTHONPATH=src python3 -m adjustments_agent --data data --out output
python3 -m pytest -q tests    # 18 tests, ~0.1s
# optional: Claude writes the wording (needs `pip install anthropic` and ANTHROPIC_API_KEY)
PYTHONPATH=src python3 -m adjustments_agent --llm
```
Outputs in `output/`: `report.md` (finance-user view), `audit_log.jsonl`, `adjusted_tb_lines.csv`
(every TB row + every posted adjustment line, each with a `source` pointer), `adjusted_tb_summary.csv`.

## Result on the bundle
| Decision | Entries |
|---|---|
| ACCEPT (5) | JE-004, 006, 007, 009, 010 |
| ACCEPT_WITH_WARNINGS (2) | JE-001 (material, 12.5% of pre-tax income), JE-003 (FX reval not reproducible; no currency) |
| QUARANTINE (3) | JE-002 (off by 3,500.00), JE-005 (account 6315 unknown), JE-008 (circular, nets to zero) |

`docs/data_findings.md` lists every defect I found in the whole bundle, including ones the PDF did not mention.

## How it is built (and why it is not an agent swarm)
```
load (Decimal) -> validate per entry [deterministic rules] -> decide -> post non-quarantined -> explain -> write
```
- **All decisions are deterministic code** (`rules.py`). Accept/quarantine is a pure function of findings.
  Money is `Decimal`; balance check has zero tolerance (a 1-cent gap is a gap).
- **The LLM has one job: wording.** (`explain.py`). It receives only structured findings and returns JSON.
  A grounding gate rejects any text containing a figure or account code that is not in the evidence, and the
  deterministic template is used instead. It also falls back on bad JSON or API errors. It cannot change a decision.
- **Nothing is auto-fixed.** For JE-005 the suggester ranks 6310 at 0.70, below the 0.90 auto-map threshold, so it
  is shown as a suggestion. It also tells the user that mapping to 6310 would make the entry cancel to zero,
  which a pure fuzzy-matcher would have walked straight into.
- **Independent recomputation** for FX entries: the booked reval is re-derived from the rate table under both
  readings of the TB amount rather than trusted.
- **Traceability:** each output row carries `TB:<csv line>` or `ADJ:<entry>#<line>`; `audit_log.jsonl` stores an
  entry hash, rule findings with evidence, and the explanation producer. Same inputs -> byte-identical output
  (tested), no timestamps.
- Quarantined entries never reach the ledger; posted adjustments are asserted to balance in aggregate.

A single-pass pipeline is enough here: there is no step where the system must *choose what to do next* from
open-ended observations. An agent loop would add cost and variance without adding capability. In the full
design the agentic part earns its keep in the Mapper (ambiguous accounts) and in drafting remediation, never in
arithmetic or the accept/reject gate.

## Honest limits
- **The live LLM path was not run against the real API** in my environment (no key). It is exercised in tests with
  stub LLMs (faithful, hallucinating, garbage, timing out), and the template path is what produced `output/`.
- The grounding gate checks numbers and account codes, not meaning: a fluent wrong *claim* using real numbers
  would pass. Mitigation in production: a second deterministic claim check, plus human review of quarantines.
- Structural validity is not correctness (see findings doc). TB amounts are summarised at face value per
  (account, currency); FX translation belongs to another stage and is blocked on the TB-currency question.
- JE-003 posts to the USD sub-balance of 1110 by assumption (flagged in `adjusted_tb_lines.csv`).
- Rules are single-entity; multi-entity IC elimination would need counterparty fields that this data lacks.
- No SOCIE/cash-flow checks here: the T5/T6 anomalies (PPE jump, unexplained RE change) would be caught there.
