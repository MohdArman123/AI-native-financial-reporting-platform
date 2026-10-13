"""Deterministic validation of manual journal entries.

Design rule: every decision (ACCEPT / ACCEPT_WITH_WARNINGS / QUARANTINE) is made here, in plain
code, from structured evidence. The LLM only *explains* findings afterwards (see explain.py).
BLOCK = we are certain the entry cannot be posted as-is. WARN = posts provisionally, needs a human.
"""
from __future__ import annotations
import hashlib, json, re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from collections import defaultdict

from .loader import D0, TBRow

RULESET_VERSION = "2024Q4-r1"
AUTO_MAP_THRESHOLD = Decimal("0.90")   # below this a candidate account is only ever a *suggestion*
FX_TOLERANCE = Decimal("0.01")         # 1% tolerance when re-computing a booked FX reval
MATERIALITY_PCT = Decimal("0.05")      # entries > 5% of pre-adjustment pre-tax income need sign-off
FX_PL_ACCOUNTS = {"7300", "7310"}
STOP = {"the", "a", "of", "and", "from", "to", "out", "into", "old", "code", "reclass", "reclassify"}


def money(d: Decimal) -> str:
    return f"{d:,.2f}"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str                 # BLOCK | WARN
    entry_id: str | None
    lines: tuple = ()
    evidence: dict = field(default_factory=dict)

    def to_json(self):
        return {"code": self.code, "severity": self.severity, "entry_id": self.entry_id,
                "lines": list(self.lines), "evidence": self.evidence}


@dataclass
class Context:
    coa: dict
    tb: list[TBRow]
    fx: dict
    period: str
    functional_ccy: str
    pretax_income: Decimal = D0
    seen: dict = field(default_factory=dict)  # signature -> first entry id

    @classmethod
    def build(cls, coa, tb, fx, adj):
        # profit = credits - debits over P&L accounts, before tax (8xxx)
        pre = sum((r.credit - r.debit) for r in tb
                  if coa.get(r.account, {}).get("statement") == "PL" and not r.account.startswith("8"))
        return cls(coa, tb, fx, adj["period"], adj.get("functional_currency", "USD"), pre)

    def tb_net(self, account: str, ccy: str | None = None) -> Decimal:
        return sum((r.net for r in self.tb if r.account == account and (ccy is None or r.currency == ccy)), D0)

    def currencies(self, account: str) -> set[str]:
        return {r.currency for r in self.tb if r.account == account}

    def period_bounds(self):
        y, q = self.period.split("-Q")
        y, q = int(y), int(q)
        start = date(y, 3 * (q - 1) + 1, 1)
        end = date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
        return start, date.fromordinal(end.toordinal() - 1)


# ---------------------------------------------------------------- candidate account suggestions
def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", s.lower()) if t not in STOP and len(t) > 2}


def suggest_accounts(code: str, text: str, coa: dict, k: int = 3) -> list[dict]:
    """Rank COA leaf accounts for an unknown code. Heuristic, explainable, and *never auto-applied*
    unless score >= AUTO_MAP_THRESHOLD (which no signal combination here can reach on its own)."""
    want = _tokens(text)
    out = []
    for c, a in coa.items():
        if a["type"] == "Header":
            continue
        score = Decimal("0")
        if c[:3] == code[:3]:
            score += Decimal("0.50")
        elif c[:2] == code[:2]:
            score += Decimal("0.20")
        name = _tokens(a["name"])
        if name and want:
            score += Decimal("0.40") * Decimal(len(name & want)) / Decimal(len(name))
        if score > 0:
            out.append({"account": c, "name": a["name"], "score": f"{min(score, Decimal('0.99')):.2f}"})
    return sorted(out, key=lambda x: (-Decimal(x["score"]), x["account"]))[:k]


# ---------------------------------------------------------------- per-entry validation
def entry_hash(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _line_amounts(line):
    return Decimal(line.get("debit", 0) or 0), Decimal(line.get("credit", 0) or 0)


def validate_entry(entry: dict, ctx: Context) -> list[Finding]:
    eid = entry.get("id", "<no id>")
    F = lambda code, sev, lines=(), **ev: Finding(code, sev, eid, tuple(lines), ev)
    out: list[Finding] = []
    lines = entry.get("lines") or []

    # R01 schema -- if this fails nothing else can be trusted, so stop early
    bad = []
    if len(lines) < 2:
        bad.append("fewer than two lines")
    for i, ln in enumerate(lines, 1):
        try:
            d, c = _line_amounts(ln)
        except Exception:
            bad.append(f"line {i}: amount is not numeric"); continue
        if not ln.get("account"):
            bad.append(f"line {i}: missing account")
        if d < 0 or c < 0:
            bad.append(f"line {i}: negative amount")
        if (d > 0) == (c > 0):
            bad.append(f"line {i}: must have exactly one of debit/credit")
    if bad:
        return [F("SCHEMA_ERROR", "BLOCK", problems=bad)]

    # R02 debits == credits (Decimal, exact -- no tolerance: a 0.01 gap is still a gap)
    dr = sum((_line_amounts(l)[0] for l in lines), D0)
    cr = sum((_line_amounts(l)[1] for l in lines), D0)
    if dr != cr:
        out.append(F("UNBALANCED", "BLOCK", lines=range(1, len(lines) + 1),
                     total_debit=money(dr), total_credit=money(cr), difference=money(abs(dr - cr)),
                     higher_side="debit" if dr > cr else "credit",
                     line_detail=[{"line": i, "account": l["account"], "debit": money(_line_amounts(l)[0]),
                                   "credit": money(_line_amounts(l)[1])} for i, l in enumerate(lines, 1)]))

    # R03 accounts exist and are postable
    text = f'{entry.get("description", "")} ' + " ".join(l.get("memo", "") for l in lines)
    for i, l in enumerate(lines, 1):
        a = ctx.coa.get(l["account"])
        if a is None:
            cands = suggest_accounts(l["account"], text, ctx.coa)
            top = cands[0]["account"] if cands else None
            net_zero = False
            if top:  # would remapping to the top candidate collapse the entry to zero net effect?
                n = defaultdict(lambda: D0)
                for m in lines:
                    d, c = _line_amounts(m)
                    n[top if m is l else m["account"]] += d - c
                net_zero = all(v == 0 for v in n.values())
            out.append(F("UNKNOWN_ACCOUNT", "BLOCK", lines=[i], account=l["account"], candidates=cands,
                         auto_map_threshold=str(AUTO_MAP_THRESHOLD),
                         would_net_to_zero_if_mapped_to_top=net_zero))
        elif a["type"] == "Header":
            out.append(F("HEADER_POSTING", "BLOCK", lines=[i], account=l["account"], name=a["name"]))

    # R04 no economic effect (circular / self-offsetting). Same-account both sides, or nets to zero everywhere.
    net = defaultdict(lambda: D0)
    for l in lines:
        d, c = _line_amounts(l)
        net[l["account"]] += d - c
    if all(v == 0 for v in net.values()) and dr == cr:
        accts = sorted(net)
        out.append(F("NO_ECONOMIC_EFFECT", "BLOCK", lines=range(1, len(lines) + 1), accounts=accts,
                     amount=money(dr),
                     intercompany=any("intercompany" in ctx.coa.get(a, {}).get("name", "").lower() for a in accts),
                     counterparty_account_in_coa=any("intercompany" in a["name"].lower() and a["type"] == "Asset"
                                                      for a in ctx.coa.values())))

    # R05 date inside the period
    try:
        dt = date.fromisoformat(entry["date"])
        s, e = ctx.period_bounds()
        if not s <= dt <= e:
            out.append(F("DATE_OUT_OF_PERIOD", "BLOCK", date=entry["date"], period_start=str(s), period_end=str(e)))
    except (KeyError, ValueError):
        out.append(F("DATE_OUT_OF_PERIOD", "BLOCK", date=str(entry.get("date")), reason="missing or unparseable"))

    # R06 exact duplicate of an earlier entry
    sig = json.dumps(sorted((l["account"], str(_line_amounts(l)[0]), str(_line_amounts(l)[1])) for l in lines))
    if sig in ctx.seen:
        out.append(F("DUPLICATE_ENTRY", "BLOCK", duplicate_of=ctx.seen[sig]))
    else:
        ctx.seen[sig] = eid

    # R07 lines on a multi-currency account carry no currency -> posting currency is an assumption
    for i, l in enumerate(lines, 1):
        ccys = ctx.currencies(l["account"])
        if len(ccys) > 1 and not l.get("currency"):
            out.append(F("CURRENCY_UNSPECIFIED", "WARN", lines=[i], account=l["account"],
                         tb_currencies=sorted(ccys), assumed=ctx.functional_ccy))

    # R08 FX revaluation entries are re-computed independently from the rate table
    fx_line = next((l for l in lines if l["account"] in FX_PL_ACCOUNTS), None)
    if fx_line is not None and dr == cr:
        out.append(_check_fx(entry, fx_line, lines, ctx, F))

    # R09 materiality -> reviewer sign-off even when otherwise clean
    if ctx.pretax_income > 0 and dr > MATERIALITY_PCT * ctx.pretax_income:
        out.append(F("MATERIAL_ENTRY", "WARN", amount=money(dr), pretax_income=money(ctx.pretax_income),
                     pct=f"{dr / ctx.pretax_income * 100:.1f}", threshold_pct=f"{MATERIALITY_PCT * 100:.0f}"))

    return [f for f in out if f is not None]


def _check_fx(entry, fx_line, lines, ctx, F):
    booked = sum(_line_amounts(l)[0] for l in lines if l is not fx_line)  # amount hitting the BS leg
    m = re.search(r"\b(EUR|GBP)\b", entry.get("description", ""))
    if not m:
        return F("FX_UNVERIFIED", "WARN", reason="currency_not_identifiable", booked=money(booked))
    ccy = m.group(1)
    bs = next((l for l in lines if l is not fx_line), None)
    local = ctx.tb_net(bs["account"], ccy)
    end, opening = ctx.fx.get((ccy, "period_end")), ctx.fx.get((ccy, "opening"))
    if end is None or opening is None:
        return F("FX_UNVERIFIED", "WARN", reason="missing_rate", currency=ccy, booked=money(booked),
                 missing=[t for t, v in (("period_end", end), ("opening", opening)) if v is None])
    a = local * (end - opening)                       # TB figure is in EUR units
    b = (local / end) * (end - opening)               # TB figure is already USD at period-end
    ok = any(abs(booked - x) <= FX_TOLERANCE * x for x in (a, b))
    if ok:
        return None
    return F("FX_UNVERIFIED", "WARN", reason="not_reproducible", currency=ccy, booked=money(booked),
             tb_balance=money(local), opening_rate=str(opening), period_end_rate=str(end),
             expected_if_tb_in_local=money(a), expected_if_tb_in_usd=money(b),
             tolerance_pct=str(FX_TOLERANCE * 100))


def decide(findings: list[Finding]) -> str:
    if any(f.severity == "BLOCK" for f in findings):
        return "QUARANTINE"
    return "ACCEPT_WITH_WARNINGS" if findings else "ACCEPT"


def batch_findings(adj: dict) -> list[Finding]:
    """Properties of the batch itself (not any single entry)."""
    out = []
    audit_fields = {"prepared_by", "approved_by", "posted_by", "preparer", "approver"}
    if not any(audit_fields & set(e) for e in adj.get("entries", [])):
        out.append(Finding("NO_APPROVAL_METADATA", "WARN", None, (), {
            "entries": len(adj.get("entries", [])),
            "detail": "no preparer/approver fields on any entry; maker-checker cannot be evidenced"}))
    return out
