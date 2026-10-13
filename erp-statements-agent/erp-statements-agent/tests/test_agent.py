import copy, json, sys
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from adjustments_agent.cli import run
from adjustments_agent.explain import explain, grounded
from adjustments_agent.loader import load_adjustments, load_coa, load_fx, load_tb
from adjustments_agent.rules import Context, decide, validate_entry

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def ctx_and_adj():
    adj = load_adjustments(DATA / "manual_adjustments.json")
    ctx = Context.build(load_coa(DATA / "chart_of_accounts.csv"), load_tb(DATA / "trial_balance.csv"),
                        load_fx(DATA / "fx_rates.csv"), adj)
    return ctx, adj


def codes(ctx, entry):
    return {f.code for f in validate_entry(entry, ctx)}


def entry(adj, eid):
    return next(e for e in adj["entries"] if e["id"] == eid)


# ---- the seeded defects in the real bundle
def test_expected_decisions_on_messy_bundle(tmp_path):
    res = run(DATA, tmp_path)
    got = {r["entry"]["id"]: r["decision"] for r in res["results"]}
    assert got["JE-002"] == got["JE-005"] == got["JE-008"] == "QUARANTINE"
    assert got["JE-001"] == got["JE-003"] == "ACCEPT_WITH_WARNINGS"
    assert [k for k, v in got.items() if v == "ACCEPT"] == ["JE-004", "JE-006", "JE-007", "JE-009", "JE-010"]


def test_unbalanced_reports_exact_difference(ctx_and_adj):
    ctx, adj = ctx_and_adj
    f = next(f for f in validate_entry(entry(adj, "JE-002"), ctx) if f.code == "UNBALANCED")
    assert f.evidence["difference"] == "3,500.00" and f.evidence["higher_side"] == "debit"


def test_unknown_account_is_suggested_never_applied(ctx_and_adj):
    ctx, adj = ctx_and_adj
    f = next(f for f in validate_entry(entry(adj, "JE-005"), ctx) if f.code == "UNKNOWN_ACCOUNT")
    assert f.evidence["candidates"][0]["account"] == "6310"
    assert Decimal(f.evidence["candidates"][0]["score"]) < Decimal(f.evidence["auto_map_threshold"])
    assert f.evidence["would_net_to_zero_if_mapped_to_top"] is True


def test_circular_ic_entry_detected(ctx_and_adj):
    ctx, adj = ctx_and_adj
    f = next(f for f in validate_entry(entry(adj, "JE-008"), ctx) if f.code == "NO_ECONOMIC_EFFECT")
    assert f.evidence["intercompany"] and not f.evidence["counterparty_account_in_coa"]


def test_fx_reval_recomputed_not_trusted(ctx_and_adj):
    ctx, adj = ctx_and_adj
    f = next(f for f in validate_entry(entry(adj, "JE-003"), ctx) if f.code == "FX_UNVERIFIED")
    assert f.evidence["expected_if_tb_in_local"] == "19,809.60"


def test_missing_gbp_period_end_rate_is_reported(ctx_and_adj):
    ctx, adj = ctx_and_adj
    e = copy.deepcopy(entry(adj, "JE-003")); e["description"] = e["description"].replace("EUR", "GBP")
    f = next(f for f in validate_entry(e, ctx) if f.code == "FX_UNVERIFIED")
    assert f.evidence["reason"] == "missing_rate" and f.evidence["missing"] == ["period_end"]


# ---- generic rules
def mk(lines, **kw):
    return {"id": "X", "description": "t", "date": "2024-12-01", "source": "s", "lines": lines, **kw}


def test_decimal_not_float(ctx_and_adj):
    ctx, _ = ctx_and_adj  # 0.1 + 0.2 == 0.3 only with Decimal
    e = mk([{"account": "6100", "debit": Decimal("0.1")}, {"account": "6100", "debit": Decimal("0.2")},
            {"account": "2120", "credit": Decimal("0.3")}])
    assert "UNBALANCED" not in codes(ctx, e)


def test_one_cent_gap_is_a_gap(ctx_and_adj):
    ctx, _ = ctx_and_adj
    e = mk([{"account": "6100", "debit": Decimal("100.01")}, {"account": "2120", "credit": Decimal("100.00")}])
    assert "UNBALANCED" in codes(ctx, e)


@pytest.mark.parametrize("bad", [
    [{"account": "6100", "debit": Decimal(5), "credit": Decimal(5)}, {"account": "2120", "credit": Decimal(0)}],
    [{"account": "6100", "debit": Decimal(-5)}, {"account": "2120", "credit": Decimal(-5)}],
    [{"account": "6100", "debit": Decimal(5)}],
])
def test_schema_errors_block(ctx_and_adj, bad):
    ctx, _ = ctx_and_adj
    assert codes(ctx, mk(bad)) == {"SCHEMA_ERROR"}


def test_date_outside_period_and_header_posting_and_duplicate(ctx_and_adj):
    ctx, _ = ctx_and_adj
    ln = [{"account": "6000", "debit": Decimal(10)}, {"account": "2120", "credit": Decimal(10)}]
    assert {"DATE_OUT_OF_PERIOD", "HEADER_POSTING"} <= codes(ctx, mk(ln, date="2025-01-02"))
    ctx.seen.clear()
    assert "DUPLICATE_ENTRY" not in codes(ctx, mk(ln)) and "DUPLICATE_ENTRY" in codes(ctx, mk(ln))
    ctx.seen.clear()


# ---- pipeline properties
def test_idempotent_byte_identical(tmp_path):
    run(DATA, tmp_path / "a"); run(DATA, tmp_path / "b")
    for f in ("report.md", "audit_log.jsonl", "adjusted_tb_lines.csv", "adjusted_tb_summary.csv"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_quarantined_never_posted_and_original_rows_untouched(tmp_path):
    res = run(DATA, tmp_path)
    src = [r["source"] for r in res["rows"]]
    assert not any(s.startswith(("ADJ:JE-002", "ADJ:JE-005", "ADJ:JE-008")) for s in src)
    assert sum(s.startswith("TB:") for s in src) == len(load_tb(DATA / "trial_balance.csv"))
    assert res["profit_impact"] == Decimal("-1211800.0")


# ---- the LLM gate
def _llm_returning(text):
    return lambda system, user: text


def _je002(ctx_and_adj):
    ctx, adj = ctx_and_adj
    e = entry(adj, "JE-002"); return e, validate_entry(e, ctx)


def test_llm_hallucinated_number_rejected(ctx_and_adj):
    e, fs = _je002(ctx_and_adj)
    bad = json.dumps({"headline": "Unbalanced", "next_step": "Fix it.",
                      "explanation": "Debits 28,500.00 exceed credits by 3,600.00."})
    out = explain(e, fs, "QUARANTINE", _llm_returning(bad))
    assert out["producer"] == "template_fallback" and "3600" in out["llm_rejected"]


def test_llm_grounded_text_accepted(ctx_and_adj):
    e, fs = _je002(ctx_and_adj)
    good = json.dumps({"headline": "Unbalanced entry", "next_step": "Ask the controller which amount is right.",
                       "explanation": "Debits of 28,500.00 do not match credits of 25,000.00; the gap is 3,500.00."})
    assert explain(e, fs, "QUARANTINE", _llm_returning(good))["producer"] == "llm"


def test_llm_garbage_or_error_falls_back(ctx_and_adj):
    e, fs = _je002(ctx_and_adj)
    assert explain(e, fs, "QUARANTINE", _llm_returning("sorry, I can't"))["producer"] == "template_fallback"
    def boom(s, u): raise TimeoutError("api down")
    assert explain(e, fs, "QUARANTINE", boom)["producer"] == "template_fallback"


def test_llm_cannot_change_decision(ctx_and_adj):
    ctx, adj = ctx_and_adj
    e = entry(adj, "JE-002")
    fs = validate_entry(e, ctx)
    explain(e, fs, "QUARANTINE", _llm_returning(json.dumps({"headline": "fine", "explanation": "ok", "next_step": "post it"})))
    assert decide(fs) == "QUARANTINE"  # decision is a pure function of findings
