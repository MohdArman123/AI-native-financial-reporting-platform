"""Deterministic ingestion. Money is Decimal everywhere; floats never touch an amount."""
from __future__ import annotations
import csv, hashlib, json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

D0 = Decimal("0")


@dataclass(frozen=True)
class TBRow:
    src_row: int  # 1-based line number in the CSV file (header = line 1) -> audit anchor
    account: str
    name: str
    currency: str
    debit: Decimal
    credit: Decimal

    @property
    def net(self) -> Decimal:  # debit-positive
        return self.debit - self.credit


def _read_csv(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        yield from enumerate(csv.DictReader(f), start=2)


def load_coa(path: Path) -> dict[str, dict]:
    return {
        r["account_code"].strip(): {
            "name": r["account_name"].strip(), "type": r["account_type"].strip(),
            "parent": r["parent_code"].strip() or None, "statement": r["statement"].strip(),
            "cf": r["cf_category"].strip() or None, "normal": r["normal_balance"].strip() or None,
        }
        for _, r in _read_csv(path)
    }


def load_tb(path: Path) -> list[TBRow]:
    return [TBRow(i, r["account_code"].strip(), r["account_name"].strip(), r["currency"].strip(),
                  Decimal(r["debit"] or "0"), Decimal(r["credit"] or "0")) for i, r in _read_csv(path)]


def load_fx(path: Path) -> dict[tuple[str, str], Decimal]:
    return {(r["currency"], r["rate_type"]): Decimal(r["rate"]) for _, r in _read_csv(path)}


def load_adjustments(path: Path) -> dict:
    # parse_float=Decimal: 850000.0 -> Decimal('850000.0'), never a binary float
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def input_hash(data_dir: Path) -> str:
    h = hashlib.sha256()
    for name in ("chart_of_accounts.csv", "fx_rates.csv", "manual_adjustments.json", "trial_balance.csv"):
        h.update((data_dir / name).read_bytes())
    return h.hexdigest()
