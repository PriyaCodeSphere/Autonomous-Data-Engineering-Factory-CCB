"""Synthetic Test Data Agent — Faker-based CC&B fixtures, respects PII classification.

Produces small QA-friendly CSVs for a subset of the CC&B tables so QA teams
can run the pipeline in an isolated environment without touching production PII.
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta

from faker import Faker

from .base import Agent, RunContext


N_PERSONS = 200
N_ACCOUNTS = 200            # 1:1
N_BILLS = 600               # 3 months
N_PAYMENTS = 500

CIS_DIVISIONS = ["BKLYN", "BRONX", "MHTN", "QNS", "WSTCH"]
CUST_CLASSES = ["RES", "COM", "IND"]
SA_TYPES = ["EL-RES", "EL-COM", "GS-RES", "GS-COM"]
BILL_CYCLES = ["M01", "M02", "M03"]


class SynthAgent(Agent):
    id = "synth"
    name = "Synthetic Test Data Agent"
    stage = "synth"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)

        seed = 42
        random.seed(seed)
        fake = Faker("en_US")
        Faker.seed(seed)

        self.emit(ctx, f"Seed={seed} · generating {N_PERSONS} persons · "
                       f"{N_ACCOUNTS} accounts · {N_BILLS} bills · {N_PAYMENTS} payments")

        # -- Person (PII from Faker; hashes will be applied at staging) --
        persons = []
        for i in range(1, N_PERSONS + 1):
            div = random.choice(CIS_DIVISIONS)
            persons.append({
                "PER_ID":         f"9{i:09d}",
                "PER_OR_BUS_FLG": random.choices(["P", "B"], weights=[0.9, 0.1])[0],
                "LANGUAGE_CD":    "ENG",
                "ADDRESS1":       fake.street_address(),
                "CITY":           fake.city(),
                "STATE":          "NY",
                "POSTAL":         fake.zipcode_in_state("NY"),
                "COUNTRY":        "USA",
                "EMAILID":        fake.email(),
                "HOUSE_TYPE":     random.choice(["SFR", "MFR", "APT"]),
                "CIS_DIVISION":   div,
            })
        p = _write_csv(ctx, ("synth", "person.csv"), persons)
        self.artifact(ctx, "person.csv", p, preview=_preview_csv(p))

        # -- Account --
        accounts = []
        for i, per in enumerate(persons, start=1):
            cust = "RES" if per["PER_OR_BUS_FLG"] == "P" else random.choice(["COM", "IND"])
            accounts.append({
                "ACCT_ID":       f"8{i:09d}",
                "PER_ID":        per["PER_ID"],
                "CIS_DIVISION":  per["CIS_DIVISION"],
                "CUST_CL_CD":    cust,
                "COLL_CL_CD":    f"{cust}-STD",
                "BILL_CYC_CD":   random.choice(BILL_CYCLES),
                "CURRENCY_CD":   "USD",
                "SETUP_DT":      fake.date_between(start_date="-6y", end_date="-6M").isoformat(),
            })
        p = _write_csv(ctx, ("synth", "account.csv"), accounts)
        self.artifact(ctx, "account.csv", p, preview=_preview_csv(p))

        # -- Bill (3 months per account) --
        bill_rows = []
        bill_seq = 0
        for a in accounts:
            base = 6.5 if a["CUST_CL_CD"] == "RES" else 8.0
            for months_back in (1, 2, 3):
                bill_seq += 1
                if bill_seq > N_BILLS:
                    break
                amt = round(max(15.0, min(20_000.0, random.lognormvariate(base, 0.8))), 2)
                bill_dt = date.today() - timedelta(days=30 * months_back)
                bill_rows.append({
                    "BILL_ID":            f"S-BILL{bill_seq:06d}",
                    "ACCT_ID":            a["ACCT_ID"],
                    "BILL_CYC_CD":        a["BILL_CYC_CD"],
                    "BILL_STAT_FLG":      "50",
                    "BILL_DT":            bill_dt.isoformat(),
                    "DUE_DT":             (bill_dt + timedelta(days=20)).isoformat(),
                    "LATE_PAY_CHARGE_SW": "N",
                    "TOTAL_AMOUNT":       amt,
                })
        p = _write_csv(ctx, ("synth", "bill.csv"), bill_rows)
        self.artifact(ctx, "bill.csv", p, preview=_preview_csv(p))

        # -- Payment --
        pay_rows = []
        for i in range(1, N_PAYMENTS + 1):
            b = random.choice(bill_rows)
            pay_amt = round(float(b["TOTAL_AMOUNT"]) * random.choice([1.0, 1.0, 0.75, 1.10]), 2)
            pay_dt = date.fromisoformat(b["BILL_DT"]) + timedelta(days=random.randint(1, 25))
            pay_rows.append({
                "PAY_ID":         f"S-PAY{i:06d}",
                "ACCT_ID":        b["ACCT_ID"],
                "BILL_ID":        b["BILL_ID"],
                "PAY_AMT":        pay_amt,
                "PAY_STATUS_FLG": "50",
                "PAY_DT":         pay_dt.isoformat(),
                "TENDER_TYPE_CD": random.choice(["ACH", "CHECK", "CARD"]),
            })
        p = _write_csv(ctx, ("synth", "payment.csv"), pay_rows)
        self.artifact(ctx, "payment.csv", p, preview=_preview_csv(p))

        # -- Generator config for reproducibility --
        cfg = (
            "# testing/synth/oracle_ccb.yml\n"
            f"seed: {seed}\n"
            "volume:\n"
            f"  person:  {N_PERSONS}\n"
            f"  account: {N_ACCOUNTS}\n"
            f"  bill:    {N_BILLS}\n"
            f"  payment: {N_PAYMENTS}\n"
            "integrity:\n"
            "  account.PER_ID: {fk_to: person.PER_ID}\n"
            "  bill.ACCT_ID:   {fk_to: account.ACCT_ID}\n"
            "  payment.BILL_ID:{fk_to: bill.BILL_ID}\n"
            "pii:\n"
            "  strategy: from_faker\n"
            "  locale: en_US\n"
            "  reproducible: true\n"
        )
        p = ctx.write_text(("synth", "generator.yml"), cfg)
        self.artifact(ctx, "generator.yml", p, preview=cfg)

        ctx.outputs["synth"] = {
            "person_rows":  N_PERSONS,
            "account_rows": N_ACCOUNTS,
            "bill_rows":    N_BILLS,
            "payment_rows": N_PAYMENTS,
            "seed": seed,
        }
        total = N_PERSONS + N_ACCOUNTS + N_BILLS + N_PAYMENTS
        self.done(ctx, f"generated {total:,} synthetic rows")
        return ctx.outputs["synth"]


def _write_csv(ctx: RunContext, parts: tuple[str, ...], rows: list[dict]):
    p = ctx.artifact_path(*parts)
    if not rows:
        p.write_text("", encoding="utf-8")
        return p
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return p


def _preview_csv(path) -> str:
    lines = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 6:
                break
            lines.append(line.rstrip())
    return "\n".join(lines)
