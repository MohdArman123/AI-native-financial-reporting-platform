# Defects found in the input bundle

Found by profiling the raw files (nothing was cleaned first). Items marked **(PDF)** were listed in the
assignment; the rest were not. "Handled by" says whether the adjustments slice deals with it or it belongs to
another stage of the architecture.

## Trial balance (`trial_balance.csv`)
| # | Finding | Evidence | Handled by |
|---|---|---|---|
| T1 | **(PDF)** Does not balance | Face-value sum: credits exceed debits by **4,800.00** | Reconciliation / validator stage |
| T2 | **The imbalance is only small if foreign rows are NOT translated.** Translating EUR @1.095 and GBP @1.264 (avg, since no period-end) makes it ~**+182,460** debit-heavy; with GBP @ opening 1.251 it is ~+177,100. USD rows alone are off by 1,242,500, almost exactly the EUR+GBP cash face value (1,237,700). | computed | **Blocking question #1:** are `debit`/`credit` in transaction currency or already USD? |
| T3 | **(PDF)** Duplicate account `6310` (two USD rows: 245,000 and 38,500) | rows 47-48 | Mapper stage; amounts differ so it is *not* an exact duplicate: needs a human |
| T4 | **(PDF)** Orphan `9999 Suspense - Unmapped` (12,400 Dr) | not in COA | Mapper stage |
| T5 | `1210` PPE cost jumps **+5,207,700** (11.8M -> 17,007,700) while cash moves only +411,900 and debt falls. The odd ending digits look like a balancing plug. | vs prior TB | Validator: cash-flow sanity check will fail loudly if this is real |
| T6 | `3200` Retained earnings 7,240,000 vs prior 5,180,000 (+2,060,000) with no equity movement explanation | vs prior TB | SOCIE stage: unexplained roll-forward gap (dividends? restatement?) |

## Prior-period TB
| # | Finding | Handled by |
|---|---|---|
| P1 | **(PDF)** `6905 Sundry Operating Expenses` is not in the COA; probably the old code for `6900 Other Operating Expenses` (renamed/recoded) | Mapper: propose 6900, confidence < threshold -> human |
| P2 | Prior TB is itself out of balance by 2,832,800 Dr and contains only one P&L account (6905). It looks like a post-close/BS-only extract with a stray P&L row, so it cannot give comparative P&L. | Ingestion validator |

## Chart of accounts
| # | Finding | Handled by |
|---|---|---|
| C1 | **(PDF)** `1150 Other Current Assets` and `2170 Intercompany Payable` have cf_category = `TBD` | Cash-flow stage; LLM proposes, human confirms |
| C2 | `1290 Other Assets` is a Header with no children | COA validator (info) |
| C3 | `3200`, `3300`, `3310` have no cf_category (equity roll-forward items; fine for CF but must be explicit) | COA validator |
| C4 | `7300`/`7310` FX gain/loss are typed `Expense` with cf = Operating; unrealised FX is non-cash and needs a reconciling adjustment in an indirect cash flow | Cash-flow stage |
| C5 | `8000 Income Tax Expense` is a Header with no parent; `7000` has blank normal balance; `3300` is typed Equity (not Header) yet has a child, so it is a postable parent | COA validator |
| C6 | `4200` Sales returns is typed Revenue with Debit normal balance (contra), `1121`/`1211`/`1221` are contra-assets. Sign logic must use normal balance, not account type. | Statement builder |
| C7 | There is no Intercompany **Receivable** account at all | see JE-008 |

## FX rates
| # | Finding |
|---|---|
| F1 | **(PDF)** No `GBP period_end` rate (only average 1.264 and opening 1.251). A fallback is a policy decision (use opening? average? stop?), so the system never invents one silently. |
| F2 | `opening` rates exist for EUR/GBP but not USD (harmless, rate is 1) |

## Manual adjustments (`manual_adjustments.json`)
| Entry | Finding | Decision |
|---|---|---|
| JE-002 | **(PDF)** Debit 28,500 vs credit 25,000, off by 3,500 | QUARANTINE |
| JE-005 | **(PDF)** Account `6315` not in COA. *Not in the PDF:* if it maps to its nearest parent `6310`, the entry credits and debits the same account and cancels to zero | QUARANTINE |
| JE-008 | **(PDF)** Circular: debits and credits the same account 2170. *Not in the PDF:* a "settlement" should touch cash, and there is no IC receivable account | QUARANTINE |
| JE-003 | Booked reval 11,200 cannot be reproduced from the rate table (19,809.60 if the TB is in EUR; 18,090.96 if in USD). Line has no currency on a 3-currency account. Also an accounting-policy question: P&L (remeasurement) or OCI (translation)? | ACCEPT_WITH_WARNINGS |
| JE-001 | 850,000 = 12.5% of pre-tax income (6,806,500). Valid but material | ACCEPT_WITH_WARNINGS |
| all | No preparer / approver fields on any entry; maker-checker cannot be evidenced | batch warning |
| JE-004/006/007/009/010 | Pass every structural rule | ACCEPT |

Passing structural checks is **not** a claim that the numbers are right (is 850k the right bonus pool?).
That judgement stays with the finance team; the system's job is to make it easy for them to check.

## Questions I would ask before building more (accounting semantics, not engineering)
1. **TB currency semantics (T2).** Are TB amounts in transaction currency or already functional currency?
2. **Is the TB pre- or post-closing**, and does it already include these adjustments? (P2, T6)
3. **FX policy:** fallback when a period-end rate is missing, and P&L vs OCI for the EUR/GBP cash (JE-003).
4. **Authority:** who may release a quarantined entry or override a block, and is a provisional statement allowed?
