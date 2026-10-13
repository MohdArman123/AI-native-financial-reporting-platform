"""python -m adjustments_agent --data data --out output [--llm]

Pipeline (linear, no agent loop needed - see README for why):
  load -> validate each entry (deterministic) -> decide -> post non-quarantined -> explain -> write artifacts
Re-running on the same inputs produces byte-identical output (no timestamps, stable ordering).
"""
from __future__ import annotations
import argparse, csv, json, os
from pathlib import Path

from .explain import AnthropicLLM, explain
from .loader import input_hash, load_adjustments, load_coa, load_fx, load_tb
from .poster import abnormal_flips, ledger_lines, profit_impact, summarize
from .rules import RULESET_VERSION, Context, batch_findings, decide, entry_hash, money, validate_entry


def run(data: Path, out: Path, llm=None) -> dict:
    coa, tb = load_coa(data / "chart_of_accounts.csv"), load_tb(data / "trial_balance.csv")
    fx, adj = load_fx(data / "fx_rates.csv"), load_adjustments(data / "manual_adjustments.json")
    ctx = Context.build(coa, tb, fx, adj)

    results = []
    for e in adj["entries"]:
        fs = validate_entry(e, ctx)
        results.append({"entry": e, "findings": fs, "decision": decide(fs)})
    decisions = {r["entry"]["id"]: r["decision"] for r in results}

    rows = ledger_lines(tb, adj["entries"], decisions, ctx.functional_ccy, coa)
    summary = summarize(rows, coa)
    flips = abnormal_flips(summary, coa)
    batch = batch_findings(adj) + flips
    for r in results:
        r["explanation"] = explain(r["entry"], r["findings"], r["decision"], llm) if r["findings"] else None

    # sanity gate on our own output: posted adjustments must be balanced in aggregate
    posted = [x for x in rows if x["source"].startswith("ADJ:")]
    assert sum(x["debit"] for x in posted) == sum(x["credit"] for x in posted), "posted adjustments do not balance"

    out.mkdir(parents=True, exist_ok=True)
    meta = {"period": adj["period"], "ruleset": RULESET_VERSION, "input_sha256": input_hash(data)}
    _write_artifacts(out, results, batch, rows, summary, profit_impact(rows, coa), meta)
    return {"results": results, "batch": batch, "rows": rows, "summary": summary, "meta": meta,
            "profit_impact": profit_impact(rows, coa)}


def _write_artifacts(out, results, batch, rows, summary, impact, meta):
    # decisions + audit log
    with (out / "audit_log.jsonl").open("w") as f:
        f.write(json.dumps({"type": "run", **meta}) + "\n")
        for r in results:
            f.write(json.dumps({"type": "entry", "id": r["entry"]["id"], "entry_hash": entry_hash(r["entry"]),
                                "decision": r["decision"], "findings": [x.to_json() for x in r["findings"]],
                                "explanation": r["explanation"]}, default=str) + "\n")
        for b in batch:
            f.write(json.dumps({"type": "batch_finding", **b.to_json()}, default=str) + "\n")
    # ledger with provenance
    with (out / "adjusted_tb_lines.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "account", "currency", "debit", "credit", "assumption"])
        for r in rows:
            w.writerow([r["source"], r["account"], r["currency"], r["debit"], r["credit"], r["assumption"]])
    with (out / "adjusted_tb_summary.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account", "currency", "name", "statement", "tb_net_dr", "adjustments_net_dr", "adjusted_net_dr"])
        for s in summary:
            w.writerow([s["account"], s["currency"], s["name"], s["statement"], s["tb_net"], s["adjustments_net"], s["adjusted_net"]])
    (out / "report.md").write_text(_report(results, batch, impact, meta))


def _report(results, batch, impact, meta) -> str:
    n = lambda d: sum(1 for r in results if r["decision"] == d)
    L = [f"# Manual adjustments review - {meta['period']}", "",
         f"Ruleset `{meta['ruleset']}` | input sha256 `{meta['input_sha256'][:12]}`", "",
         f"**{n('ACCEPT')} accepted, {n('ACCEPT_WITH_WARNINGS')} posted provisionally (need review), "
         f"{n('QUARANTINE')} quarantined (not posted).**", "",
         f"Effect of posted entries on profit: **{money(impact)}** (negative = reduces profit).", "",
         "| Entry | Description | Decision | Why |", "|---|---|---|---|"]
    for r in results:
        e = r["entry"]
        why = ", ".join(f"{f.code}" for f in r["findings"]) or "-"
        L.append(f"| {e['id']} | {e['description']} | {r['decision']} | {why} |")
    L += ["", "## Entries needing attention", ""]
    for r in results:
        if not r["explanation"]:
            continue
        x, e = r["explanation"], r["entry"]
        L += [f"### {e['id']} - {x['headline']}  \n*{r['decision']}* | explained by: {x['producer']}", "",
              x["explanation"], "", f"**Next step:** {x['next_step']}", ""]
        if "llm_rejected" in x:
            L += [f"> LLM text rejected by grounding check ({x['llm_rejected']}); template used.", ""]
    if batch:
        L += ["## Batch-level findings", ""]
        for b in batch:
            L.append(f"- **{b.code}** ({b.severity}): {json.dumps(b.evidence, default=str)}")
        L.append("")
    L += ["## Assumptions this slice makes", "",
          "- Adjustment lines without a currency are posted to the functional-currency (USD) sub-balance.",
          "- TB amounts are summarised at face value per (account, currency); no FX translation happens in this slice.",
          "- Quarantined entries are never auto-corrected. Suggestions are advice for a human, not applied changes.", ""]
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data", type=Path)
    p.add_argument("--out", default="output", type=Path)
    p.add_argument("--llm", action="store_true", help="use Claude for wording (needs ANTHROPIC_API_KEY)")
    a = p.parse_args()
    llm = AnthropicLLM() if a.llm and os.getenv("ANTHROPIC_API_KEY") else None
    if a.llm and llm is None:
        print("ANTHROPIC_API_KEY not set - falling back to deterministic templates")
    res = run(a.data, a.out, llm)
    for r in res["results"]:
        print(f'{r["entry"]["id"]:7} {r["decision"]:22} {",".join(f.code for f in r["findings"])}')
    print(f'\nprofit impact of posted entries: {money(res["profit_impact"])}  -> {a.out}/report.md')


if __name__ == "__main__":
    main()
