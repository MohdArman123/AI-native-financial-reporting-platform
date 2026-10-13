# Manual adjustments review - 2024-Q4

Ruleset `2024Q4-r1` | input sha256 `ad7bd56f9528`

**5 accepted, 2 posted provisionally (need review), 3 quarantined (not posted).**

Effect of posted entries on profit: **-1,211,800.00** (negative = reduces profit).

| Entry | Description | Decision | Why |
|---|---|---|---|
| JE-001 | Accrue Q4 bonus pool | ACCEPT_WITH_WARNINGS | MATERIAL_ENTRY |
| JE-002 | Reclassify marketing spend wrongly booked to T&E | QUARANTINE | UNBALANCED |
| JE-003 | FX revaluation of EUR cash balance to period-end rate | ACCEPT_WITH_WARNINGS | CURRENCY_UNSPECIFIED, FX_UNVERIFIED |
| JE-004 | Bad debt provision top-up | ACCEPT | - |
| JE-005 | Reclass conference travel from old code | QUARANTINE | UNKNOWN_ACCOUNT |
| JE-006 | Depreciation catch-up — Q4 | ACCEPT | - |
| JE-007 | Deferred tax true-up | ACCEPT | - |
| JE-008 | Intercompany settlement — UK sub | QUARANTINE | NO_ECONOMIC_EFFECT |
| JE-009 | Accrue legal fees for ongoing litigation | ACCEPT | - |
| JE-010 | Reclass long-term debt current portion | ACCEPT | - |

## Entries needing attention

### JE-001 - Material entry - needs sign-off  
*ACCEPT_WITH_WARNINGS* | explained by: template

At 850,000.00 this entry is 12.5% of pre-adjustment pre-tax income (6,806,500.00), above the 5% review threshold. Nothing is wrong with it; it simply needs a reviewer's sign-off.

**Next step:** Reviewer to approve against the supporting workbook.

### JE-002 - Entry does not balance  
*QUARANTINE* | explained by: template

Debits total 28,500.00 but credits total 25,000.00, so the debit side is 3,500.00 higher. A journal entry must balance exactly, so it was not posted. The lines are: line 1 6300 debit 28,500.00; line 2 6310 credit 25,000.00. I have not guessed which amount is wrong - one of them is probably mistyped, or a third line is missing.

**Next step:** Check the source ('Controller note 2024-12-28') and re-submit with equal debits and credits.

### JE-003 - Currency not specified on a multi-currency account  
*ACCEPT_WITH_WARNINGS* | explained by: template

Line 1 posts to account 1110, which holds balances in EUR, GBP, USD, but the line has no currency. It was posted to the USD sub-balance by assumption. I re-computed this EUR revaluation from the rate table and could not reproduce the booked 11,200.00. Moving the EUR balance of 825,400.00 from the opening rate 1.071 to the period-end rate 1.095 gives 19,809.60 if the TB amount is in EUR, or 18,090.96 if it is already in USD. Neither is within 1.00% of the booked amount. The entry is posted provisionally.

**Next step:** Add a currency to the line so it lands in the intended sub-balance. Ask for the reval workings (balance and rates used). Also confirm whether this cash sits in a USD-functional entity (remeasurement goes to P&L, as booked) or a foreign-functional one (translation goes to OCI / FX Translation Reserve).

### JE-005 - Account not in chart of accounts  
*QUARANTINE* | explained by: template

Line 1 posts to account 6315, which is not in the chart of accounts. Closest existing account: 6310 Travel and Entertainment (match score 0.70). That score is below the 0.90 needed for automatic mapping, so nothing was changed. Note: if 6315 were mapped to 6310, the entry's debit and credit would hit the same account and cancel out, so the reclass would have no effect on the statements.

**Next step:** Confirm whether 6315 is a sub-ledger code that rolls up to an existing account, or ask for a new account to be created in the chart of accounts.

### JE-008 - Entry has no net effect (circular)  
*QUARANTINE* | explained by: template

Every line hits the same account (2170) and the debits and credits are both 320,000.00, so the entry nets to zero and would change nothing. For an intercompany settlement this usually means the other side (for example the cash account or the counterparty's receivable) was left off or posted to the wrong account. The chart of accounts also has no intercompany receivable account, so this entity cannot show the counterparty side.

**Next step:** Ask the preparer which account the second line was meant to hit, then re-submit.

## Batch-level findings

- **NO_APPROVAL_METADATA** (WARN): {"entries": 10, "detail": "no preparer/approver fields on any entry; maker-checker cannot be evidenced"}

## Assumptions this slice makes

- Adjustment lines without a currency are posted to the functional-currency (USD) sub-balance.
- TB amounts are summarised at face value per (account, currency); no FX translation happens in this slice.
- Quarantined entries are never auto-corrected. Suggestions are advice for a human, not applied changes.
