"""Apply non-quarantined entries to the TB as *additional lines*; original rows are never mutated.
Every output row carries a `source` pointer: either `TB:<csv line>` or `ADJ:<entry id>#<line>`.
"""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal

from .loader import D0, TBRow
from .rules import Finding, money


def ledger_lines(tb: list[TBRow], entries: list[dict], decisions: dict[str, str], functional_ccy: str, coa: dict):
    rows = [{"source": f"TB:{r.src_row}", "account": r.account, "currency": r.currency,
             "debit": r.debit, "credit": r.credit, "assumption": ""} for r in tb]
    for e in entries:
        if decisions[e["id"]] == "QUARANTINE":
            continue
        for i, l in enumerate(e["lines"], 1):
            ccy = l.get("currency") or functional_ccy
            rows.append({"source": f'ADJ:{e["id"]}#{i}', "account": l["account"], "currency": ccy,
                         "debit": Decimal(l.get("debit", 0)), "credit": Decimal(l.get("credit", 0)),
                         "assumption": "" if l.get("currency") else f"currency assumed {functional_ccy}"})
    return rows


def summarize(rows: list[dict], coa: dict):
    """Per (account, currency): TB net, adjustment net, post net. Face value - no FX translation here."""
    pre, adj = defaultdict(lambda: D0), defaultdict(lambda: D0)
    for r in rows:
        (pre if r["source"].startswith("TB:") else adj)[(r["account"], r["currency"])] += r["debit"] - r["credit"]
    out = []
    for k in sorted(set(pre) | set(adj)):
        a = coa.get(k[0], {})
        out.append({"account": k[0], "currency": k[1], "name": a.get("name", "<not in COA>"),
                    "statement": a.get("statement", "?"), "tb_net": pre[k], "adjustments_net": adj[k],
                    "adjusted_net": pre[k] + adj[k]})
    return out


def profit_impact(rows: list[dict], coa: dict) -> Decimal:
    """Effect of posted adjustments on profit (credit-positive over P&L accounts)."""
    return sum((r["credit"] - r["debit"] for r in rows
                if r["source"].startswith("ADJ:") and coa.get(r["account"], {}).get("statement") == "PL"), D0)


def abnormal_flips(summary: list[dict], coa: dict) -> list[Finding]:
    """Accounts whose balance changed side vs. the COA normal balance because of adjustments."""
    out = []
    for s in summary:
        n = coa.get(s["account"], {}).get("normal")
        if n not in ("Debit", "Credit") or s["adjustments_net"] == 0:
            continue
        sign = 1 if n == "Debit" else -1
        before, after = s["tb_net"] * sign, s["adjusted_net"] * sign
        if before >= 0 > after:
            out.append(Finding("ABNORMAL_BALANCE_AFTER_POSTING", "WARN", None, (),
                               {"account": s["account"], "name": s["name"], "normal": n,
                                "before": money(s["tb_net"]), "after": money(s["adjusted_net"])}))
    return out
