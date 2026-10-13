"""Plain-English explanations for a finance user.

Two producers, one gate:
  * template_explanation()  - deterministic, always available, the fallback.
  * llm_explanation()       - optional; given ONLY structured findings, asked for JSON.
  * grounded()              - every figure / account code the LLM writes must exist in the evidence,
                              otherwise the LLM text is discarded and the template is used.
The LLM can improve wording and tailor the next step. It cannot change a decision or invent a number.
"""
from __future__ import annotations
import json, os, re
from decimal import Decimal, InvalidOperation

from .rules import Finding, money

NUM = re.compile(r"(?<![\w.-])\d[\d,]*(?:\.\d+)?")


# ------------------------------------------------------------------ templates
def _t_unbalanced(e):
    side = e["higher_side"]
    detail = "; ".join(f'line {d["line"]} {d["account"]} ' + (f'debit {d["debit"]}' if d["debit"] != "0.00" else f'credit {d["credit"]}')
                       for d in e["line_detail"])
    return (f"Debits total {e['total_debit']} but credits total {e['total_credit']}, so the {side} side is "
            f"{e['difference']} higher. A journal entry must balance exactly, so it was not posted. "
            f"The lines are: {detail}. I have not guessed which amount is wrong - one of them is probably mistyped, "
            f"or a third line is missing.",
            f"Check the source ('{{source}}') and re-submit with equal debits and credits.")


def _t_unknown(e):
    c = e["candidates"]
    best = f"{c[0]['account']} {c[0]['name']} (match score {c[0]['score']})" if c else "none"
    txt = (f"Line {{line}} posts to account {e['account']}, which is not in the chart of accounts. "
           f"Closest existing account: {best}. That score is below the {e['auto_map_threshold']} needed for "
           f"automatic mapping, so nothing was changed.")
    if e["would_net_to_zero_if_mapped_to_top"]:
        txt += (f" Note: if {e['account']} were mapped to {c[0]['account']}, the entry's debit and credit would hit the "
                f"same account and cancel out, so the reclass would have no effect on the statements.")
    return txt, (f"Confirm whether {e['account']} is a sub-ledger code that rolls up to an existing account, or ask "
                 f"for a new account to be created in the chart of accounts.")


def _t_noeffect(e):
    txt = (f"Every line hits the same account ({', '.join(e['accounts'])}) and the debits and credits are both "
           f"{e['amount']}, so the entry nets to zero and would change nothing.")
    if e["intercompany"]:
        txt += (" For an intercompany settlement this usually means the other side (for example the cash account or the "
                "counterparty's receivable) was left off or posted to the wrong account.")
        if not e["counterparty_account_in_coa"]:
            txt += " The chart of accounts also has no intercompany receivable account, so this entity cannot show the counterparty side."
    return txt, "Ask the preparer which account the second line was meant to hit, then re-submit."


def _t_fx(e):
    r = e["reason"]
    if r == "not_reproducible":
        return (f"I re-computed this {e['currency']} revaluation from the rate table and could not reproduce the booked "
                f"{e['booked']}. Moving the {e['currency']} balance of {e['tb_balance']} from the opening rate "
                f"{e['opening_rate']} to the period-end rate {e['period_end_rate']} gives {e['expected_if_tb_in_local']} "
                f"if the TB amount is in {e['currency']}, or {e['expected_if_tb_in_usd']} if it is already in USD. "
                f"Neither is within {e['tolerance_pct']}% of the booked amount. The entry is posted provisionally.",
                "Ask for the reval workings (balance and rates used). Also confirm whether this cash sits in a "
                "USD-functional entity (remeasurement goes to P&L, as booked) or a foreign-functional one "
                "(translation goes to OCI / FX Translation Reserve).")
    if r == "missing_rate":
        return (f"The {e['currency']} revaluation cannot be checked because the rate table has no "
                f"{', '.join(e['missing'])} rate for {e['currency']}.", "Provide the missing rate.")
    return (f"Could not tell which currency this revaluation relates to (booked amount {e['booked']}).",
            "Name the currency in the entry description.")


def _t_ccy(e):
    return (f"Line {{line}} posts to account {e['account']}, which holds balances in {', '.join(e['tb_currencies'])}, "
            f"but the line has no currency. It was posted to the {e['assumed']} sub-balance by assumption.",
            "Add a currency to the line so it lands in the intended sub-balance.")


def _t_material(e):
    return (f"At {e['amount']} this entry is {e['pct']}% of pre-adjustment pre-tax income ({e['pretax_income']}), above "
            f"the {e['threshold_pct']}% review threshold. Nothing is wrong with it; it simply needs a reviewer's sign-off.",
            "Reviewer to approve against the supporting workbook.")


TEMPLATES = {
    "UNBALANCED": ("Entry does not balance", _t_unbalanced),
    "UNKNOWN_ACCOUNT": ("Account not in chart of accounts", _t_unknown),
    "NO_ECONOMIC_EFFECT": ("Entry has no net effect (circular)", _t_noeffect),
    "FX_UNVERIFIED": ("FX revaluation could not be verified", _t_fx),
    "CURRENCY_UNSPECIFIED": ("Currency not specified on a multi-currency account", _t_ccy),
    "MATERIAL_ENTRY": ("Material entry - needs sign-off", _t_material),
}


def template_explanation(entry: dict, findings: list[Finding]) -> dict:
    parts, steps = [], []
    for f in findings:
        if f.code in TEMPLATES:
            _, fn = TEMPLATES[f.code]
        else:
            fn = lambda e, f=f: (f"Check '{f.code}' failed: {json.dumps(e, default=str)}.", "Review the entry.")
        txt, step = fn(f.evidence)
        line = f.lines[0] if f.lines else ""
        parts.append(txt.replace("{line}", str(line)))
        steps.append(step.replace("{source}", entry.get("source", "unknown")))
    head = TEMPLATES[findings[0].code][0] if findings and findings[0].code in TEMPLATES else (findings[0].code if findings else "")
    return {"headline": head, "explanation": " ".join(parts), "next_step": " ".join(dict.fromkeys(steps)), "producer": "template"}


# ------------------------------------------------------------------ grounding gate
def _numbers_in(text: str) -> set[Decimal]:
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)     # dates
    text = re.sub(r"\bJE-\d+\b", " ", text)                # entry ids
    out = set()
    for m in NUM.findall(text):
        try:
            v = Decimal(m.replace(",", "").rstrip("."))
        except InvalidOperation:
            continue
        if v == v.to_integral_value() and v < 100:
            continue                                       # line numbers, counts
        out.add(v)
    return out


def _allowed(entry: dict, findings: list[Finding]) -> set[Decimal]:
    blob = json.dumps([f.to_json() for f in findings], default=str) + json.dumps(entry, default=str)
    return _numbers_in(blob)


def grounded(candidate: dict, entry: dict, findings: list[Finding]) -> tuple[bool, list[str]]:
    said = _numbers_in(" ".join(str(candidate.get(k, "")) for k in ("headline", "explanation", "next_step")))
    bad = sorted(str(x) for x in said - _allowed(entry, findings))
    return (not bad, bad)


# ------------------------------------------------------------------ optional LLM producer
SYSTEM = ("You explain journal-entry validation results to a finance user in plain English. Use ONLY the facts in the "
          "JSON provided. Never invent or recompute numbers or account codes. Never say an entry has been fixed or "
          "posted if the decision is QUARANTINE. Suggest next steps as questions or options for a human, not as actions "
          "taken. Reply with JSON only: {\"headline\": str, \"explanation\": str, \"next_step\": str}.")


class AnthropicLLM:
    def __init__(self, model: str | None = None):
        import anthropic  # optional dependency
        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("ADJ_AGENT_MODEL", "claude-sonnet-5-5")

    def __call__(self, system: str, user: str) -> str:
        r = self.client.messages.create(model=self.model, max_tokens=800, system=system,
                                        messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in r.content if b.type == "text")


def explain(entry: dict, findings: list[Finding], decision: str, llm=None) -> dict:
    base = template_explanation(entry, findings)
    if llm is None or not findings:
        return base
    payload = {"entry": {k: entry[k] for k in ("id", "description", "date", "source") if k in entry},
               "decision": decision, "findings": [f.to_json() for f in findings]}
    try:
        raw = llm(SYSTEM, json.dumps(payload, default=str))
        cand = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip())
        ok, bad = grounded(cand, entry, findings)
        if ok:
            return {**{k: cand.get(k, "") for k in ("headline", "explanation", "next_step")}, "producer": "llm"}
        return {**base, "producer": "template_fallback", "llm_rejected": f"ungrounded figures: {bad}"}
    except Exception as exc:  # network, JSON, schema - never let the explainer break the pipeline
        return {**base, "producer": "template_fallback", "llm_rejected": f"{type(exc).__name__}: {exc}"}
