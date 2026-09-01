"""Generate deterministic Oracle CC&B synthetic data.

Reads column schemas from ConEd_CCB_Synthetic_Database_V2.xlsx and scales the
seed rows out to ~1k records per table with Faker, preserving referential
integrity across the eight core CC&B tables the demo onboards:

  CI_PER, CI_ACCT, CI_PREM, CI_SA, CI_MTR, CI_BILL, CI_PAY, CI_CC

Writes data/person.json ... data/customer_contact.json — the entity names
match the /v1/{name}s endpoints the pipeline agent expects. Deterministic
seed=42.

Run:  python mock_source/generate_data.py
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

SEED = 42
N_PERSONS = 1_000
N_ACCOUNTS = 1_000        # 1:1 with persons
N_PREMISES = 1_000        # 1:1 with accounts (service address)
N_SERVICE_AGREEMENTS = 1_000  # 1 SA per account (single commodity)
N_METERS = 1_000          # 1 meter per SA
N_BILLS = 3_000           # 3 months of bills per account
N_PAYMENTS = 2_500        # ~2.5 payments per account
N_CUSTOMER_CONTACTS = 1_500  # ~1.5 contacts per account

DIVISIONS = ["BKLYN", "BRONX", "MHTN", "QNS", "WSTCH"]
DIVISION_CITIES = {
    "BKLYN": ("Brooklyn", "NY", ["11201", "11215", "11217", "11238"]),
    "BRONX": ("Bronx", "NY", ["10451", "10456", "10467"]),
    "MHTN":  ("New York", "NY", ["10001", "10012", "10025", "10036"]),
    "QNS":   ("Queens", "NY", ["11101", "11354", "11375"]),
    "WSTCH": ("Yonkers", "NY", ["10701", "10703", "10708"]),
}
CUST_CLASSES = ["RES", "COM", "IND"]         # residential / commercial / industrial
COLL_CLASSES = ["RES-STD", "COM-STD", "IND-STD"]
BILL_CYCLES = ["M01", "M02", "M03", "M04"]
SA_TYPES = ["EL-RES", "EL-COM", "GS-RES", "GS-COM"]  # electric/gas × res/com
METER_TYPES = ["KWH-E", "KWH-E-TOU", "MCF-G"]        # electric, TOU electric, gas
METER_MFG = ["ITRON", "LANDIS", "ELSTER"]
METER_MODELS = {
    "ITRON":  ["CENTRON-C1SR", "CENTRON-C2SR", "GENX"],
    "LANDIS": ["E350-S1", "E350-S3"],
    "ELSTER": ["A3-ALPHA", "REX2"],
}
BILL_STATUS = ["50", "60", "70"]              # complete / in-review / cancelled
PAY_STATUS = ["50", "60", "70"]                # posted / suspense / cancelled
TENDER_TYPES = ["ACH", "CHECK", "CARD", "CASH"]
CC_TYPES = ["BILL-INQ", "OUTAGE", "START-SVC", "STOP-SVC", "PAYPLAN"]
CONTACT_METHODS = ["PHONE", "EMAIL", "WEB", "CHAT"]
NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _iso_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_d(d: date) -> str:
    return d.isoformat()


def _rand_dttm_within(days_back: int) -> datetime:
    return NOW - timedelta(days=random.randint(0, days_back),
                           hours=random.randint(0, 23),
                           minutes=random.randint(0, 59))


def _weighted(items, weights):
    return random.choices(items, weights=weights, k=1)[0]


def build_persons(fake: Faker) -> list[dict]:
    persons: list[dict] = []
    for i in range(N_PERSONS):
        div = _weighted(DIVISIONS, [0.30, 0.15, 0.30, 0.20, 0.05])
        city, state, zips = DIVISION_CITIES[div]
        modified = _rand_dttm_within(days_back=180)
        # ~2% null email, ~0.5% null postal
        email = f"{fake.user_name()}@{fake.free_email_domain()}" if random.random() > 0.02 else None
        postal = random.choice(zips) if random.random() > 0.005 else None
        persons.append({
            "PER_ID":              f"1{i + 1:09d}",
            "PER_OR_BUS_FLG":      _weighted(["P", "B"], [0.88, 0.12]),
            "LANGUAGE_CD":         _weighted(["ENG", "SPA"], [0.94, 0.06]),
            "ADDRESS1":            fake.street_address(),
            "CITY":                city,
            "STATE":               state,
            "POSTAL":              postal,
            "COUNTRY":             "USA",
            "EMAILID":             email,
            "GEO_CODE":            f"{40 + random.random():.4f}-{-74 + random.random():.4f}",
            "HOUSE_TYPE":          _weighted(["SFR", "MFR", "APT", "COM"], [0.42, 0.24, 0.28, 0.06]),
            "IN_CITY_LIMIT":       _weighted(["Y", "N"], [0.90, 0.10]),
            "LS_SL_FLG":           _weighted(["N", "Y"], [0.98, 0.02]),
            "LS_SL_DESCR":         None,
            "RECV_MKTG_INFO_FLG":  _weighted(["N", "Y"], [0.72, 0.28]),
            "TIME_ZONE_CD":        "US/Eastern",
            "MODIFIED_DTTM":       _iso_dt(modified),
        })
    return persons


def build_premises(fake: Faker, persons: list[dict]) -> list[dict]:
    premises: list[dict] = []
    for i, p in enumerate(persons):
        div = _weighted(DIVISIONS, [0.30, 0.15, 0.30, 0.20, 0.05])
        city, state, zips = DIVISION_CITIES[div]
        modified = _rand_dttm_within(days_back=180)
        premises.append({
            "PREM_ID":       f"3{i + 1:09d}",
            "CIS_DIVISION":  div,
            "ADDRESS1":      p["ADDRESS1"],   # service address usually = mailing address
            "CITY":          city,
            "STATE":         state,
            "POSTAL":        random.choice(zips),
            "COUNTRY":       "USA",
            "PREM_TYPE_CD":  _weighted(["SF", "MF", "AP", "CO"], [0.42, 0.24, 0.28, 0.06]),
            "GEO_CODE":      p["GEO_CODE"],
            "OK_TO_ENTER_SW": _weighted(["Y", "N"], [0.85, 0.15]),
            "LS_SL_FLG":     "N",
            "MR_CYC_CD":     _weighted(BILL_CYCLES, [0.40, 0.25, 0.20, 0.15]),
            "TREND_AREA_CD": f"TREND-{div}",
            "MODIFIED_DTTM": _iso_dt(modified),
        })
    return premises


def build_accounts(persons: list[dict], premises: list[dict]) -> list[dict]:
    accounts: list[dict] = []
    for i, (p, prem) in enumerate(zip(persons, premises)):
        div = prem["CIS_DIVISION"]
        cust_cls = "RES" if p["PER_OR_BUS_FLG"] == "P" else _weighted(["COM", "IND"], [0.85, 0.15])
        coll_cls = {"RES": "RES-STD", "COM": "COM-STD", "IND": "IND-STD"}[cust_cls]
        # 2015-2024 setup
        setup = date(2015 + random.randint(0, 9), random.randint(1, 12), random.randint(1, 28))
        modified = _rand_dttm_within(days_back=90)
        accounts.append({
            "ACCT_ID":            f"2{i + 1:09d}",
            "CIS_DIVISION":       div,
            "CUST_CL_CD":         cust_cls,
            "COLL_CL_CD":         coll_cls,
            "BILL_CYC_CD":        prem["MR_CYC_CD"],
            "CURRENCY_CD":        "USD",
            "SETUP_DT":           _iso_d(setup),
            "MAILING_PREM_ID":    prem["PREM_ID"],
            "ACCT_MGMT_GRP_CD":   "STD",
            "PROTECT_CYC_SW":     "N",
            "NO_DEP_RVW_SW":      "N",
            "PER_ID":             p["PER_ID"],   # denormalised for demo convenience
            "MODIFIED_DTTM":      _iso_dt(modified),
        })
    return accounts


def build_service_agreements(accounts: list[dict], premises: list[dict]) -> list[dict]:
    sas: list[dict] = []
    for i, (a, prem) in enumerate(zip(accounts, premises)):
        base_type = "EL" if random.random() < 0.72 else "GS"
        sa_type = f"{base_type}-{'RES' if a['CUST_CL_CD']=='RES' else 'COM'}"
        # SA started within ~30 days of account setup
        start = date.fromisoformat(a["SETUP_DT"]) + timedelta(days=random.randint(0, 30))
        modified = _rand_dttm_within(days_back=60)
        # ~4% closed (END_DT set), rest active
        end = None
        status = "20"  # active
        if random.random() < 0.04:
            end = _iso_d(start + timedelta(days=random.randint(365, 3000)))
            status = "70"  # closed
        sas.append({
            "SA_ID":          f"4{i + 1:09d}",
            "ACCT_ID":        a["ACCT_ID"],
            "SA_TYPE_CD":     sa_type,
            "SA_STATUS_FLG":  status,
            "CIS_DIVISION":   a["CIS_DIVISION"],
            "CHAR_PREM_ID":   prem["PREM_ID"],
            "START_DT":       _iso_d(start),
            "END_DT":         end,
            "CURRENCY_CD":    "USD",
            "ALLOW_EST_SW":   "Y",
            "HIGH_BILL_AMT":  _weighted([200, 500, 1000, 2500], [0.30, 0.45, 0.20, 0.05]),
            "MODIFIED_DTTM":  _iso_dt(modified),
        })
    return sas


def build_meters(fake: Faker, sas: list[dict]) -> list[dict]:
    meters: list[dict] = []
    for i, sa in enumerate(sas):
        # Meter type keyed off SA type
        if sa["SA_TYPE_CD"].startswith("EL"):
            mtr_type = _weighted(["KWH-E", "KWH-E-TOU"], [0.7, 0.3])
        else:
            mtr_type = "MCF-G"
        mfg = _weighted(METER_MFG, [0.55, 0.30, 0.15])
        model = random.choice(METER_MODELS[mfg])
        # ~3% retired
        retire = None
        status = "AC"
        if random.random() < 0.03:
            retire = _iso_d(date.fromisoformat(sa["START_DT"]) + timedelta(days=random.randint(365, 3000)))
            status = "RM"
        meters.append({
            "MTR_ID":          f"MTR{i + 1:07d}",
            "BADGE_NBR":       f"BDG{random.randint(100000, 999999)}",
            "SERIAL_NBR":      f"SN{random.randint(1000000, 9999999)}",
            "MTR_TYPE_CD":     mtr_type,
            "MTR_STATUS_FLG":  status,
            "MFG_CD":          mfg,
            "MODEL_CD":        model,
            "RECEIVE_DT":      _iso_d(date.fromisoformat(sa["START_DT"]) - timedelta(days=random.randint(1, 30))),
            "RETIRE_DT":       retire,
            "SA_ID":           sa["SA_ID"],
            "MODIFIED_DTTM":   _iso_dt(_rand_dttm_within(days_back=60)),
        })
    return meters


def build_bills(accounts: list[dict], sas: list[dict]) -> list[dict]:
    """3 monthly bills per account: last three cycles."""
    bills: list[dict] = []
    sa_by_acct = {sa["ACCT_ID"]: sa for sa in sas}
    for i in range(N_BILLS):
        a = accounts[i % len(accounts)]
        sa = sa_by_acct[a["ACCT_ID"]]
        months_back = i // len(accounts) + 1  # 1, 2, 3
        bill_dt = (NOW - timedelta(days=30 * months_back)).date()
        due_dt = bill_dt + timedelta(days=20)
        cycle_end = bill_dt - timedelta(days=1)
        cycle_start = cycle_end - timedelta(days=29)
        # amount: lognormal, res smaller than com
        base = 6.5 if a["CUST_CL_CD"] == "RES" else 8.0
        amount = round(max(15.0, min(25_000.0, random.lognormvariate(base, 0.8))), 2)
        high_bill = float(sa["HIGH_BILL_AMT"])
        # Status: complete unless amount > HIGH_BILL_AMT (in-review) or 1.5% overdue
        if amount > high_bill:
            status = "60"      # in-review
            late_sw = "N"
        elif months_back >= 2 and random.random() < 0.15:
            status = "50"      # complete but overdue
            late_sw = "Y"
        else:
            status = "50"
            late_sw = "N"
        # ~0.3% cancelled
        if random.random() < 0.003:
            status = "70"
            late_sw = "N"
        bills.append({
            "BILL_ID":             f"BILL{i + 1:07d}",
            "ACCT_ID":             a["ACCT_ID"],
            "BILL_CYC_CD":         a["BILL_CYC_CD"],
            "BILL_STAT_FLG":       status,
            "BILL_DT":             _iso_d(bill_dt),
            "DUE_DT":              _iso_d(due_dt),
            "COMPLETE_DTTM":       _iso_dt(datetime(bill_dt.year, bill_dt.month, bill_dt.day, 2, tzinfo=timezone.utc)),
            "WIN_START_DT":        _iso_d(cycle_start),
            "CRE_DTTM":            _iso_dt(datetime(bill_dt.year, bill_dt.month, bill_dt.day, 0, tzinfo=timezone.utc)),
            "LATE_PAY_CHARGE_SW":  late_sw,
            "ALLOW_REOPEN_SW":     "N",
            "TOTAL_AMOUNT":        amount,
            "MODIFIED_DTTM":       _iso_dt(_rand_dttm_within(days_back=30)),
        })
    return bills


def build_payments(accounts: list[dict], bills: list[dict]) -> list[dict]:
    payments: list[dict] = []
    # Index bills by account, most-recent first, to pay against
    bills_by_acct: dict[str, list[dict]] = {}
    for b in bills:
        bills_by_acct.setdefault(b["ACCT_ID"], []).append(b)
    for lst in bills_by_acct.values():
        lst.sort(key=lambda b: b["BILL_DT"], reverse=True)

    for i in range(N_PAYMENTS):
        a = accounts[i % len(accounts)]
        acct_bills = bills_by_acct.get(a["ACCT_ID"], [])
        # Randomly pay against one of the account's recent bills (usually the most-recent 2)
        if not acct_bills:
            continue
        b = random.choice(acct_bills[:2])
        bill_amount = float(b["TOTAL_AMOUNT"])
        # 92% pay exact, 5% partial, 2% overpay (anomaly), 1% odd amount
        r = random.random()
        if r < 0.92:
            pay_amt = round(bill_amount, 2)
        elif r < 0.97:
            pay_amt = round(bill_amount * random.uniform(0.30, 0.85), 2)
        elif r < 0.99:
            pay_amt = round(bill_amount * random.uniform(1.05, 1.60), 2)   # overpayment anomaly
        else:
            pay_amt = round(random.uniform(5, 200), 2)
        pay_dt = date.fromisoformat(b["BILL_DT"]) + timedelta(days=random.randint(1, 30))
        # ~1% suspense (unmatched), ~0.5% cancelled
        r2 = random.random()
        if r2 < 0.005:
            status = "70"
        elif r2 < 0.015:
            status = "60"
        else:
            status = "50"
        payments.append({
            "PAY_ID":           f"PAY{i + 1:07d}",
            "ACCT_ID":          a["ACCT_ID"],
            "PAY_AMT":          pay_amt,
            "PAY_STATUS_FLG":   status,
            "MATCH_TYPE_CD":    _weighted(["ACCT", "BILL", "OPEN"], [0.75, 0.20, 0.05]),
            "MATCH_VAL":        a["ACCT_ID"],
            "CURRENCY_CD":      "USD",
            "PAY_DT":           _iso_d(pay_dt),
            "TENDER_TYPE_CD":   _weighted(TENDER_TYPES, [0.55, 0.18, 0.22, 0.05]),
            "BILL_ID":          b["BILL_ID"],   # denormalised for demo joins
            "MODIFIED_DTTM":    _iso_dt(_rand_dttm_within(days_back=15)),
        })
    return payments


def build_customer_contacts(accounts: list[dict], persons: list[dict]) -> list[dict]:
    ccs: list[dict] = []
    per_by_acct = {a["ACCT_ID"]: a["PER_ID"] for a in accounts}
    for i in range(N_CUSTOMER_CONTACTS):
        a = accounts[i % len(accounts)]
        dttm = _rand_dttm_within(days_back=180)
        ccs.append({
            "CC_ID":              f"CC{i + 1:07d}",
            "ACCT_ID":            a["ACCT_ID"],
            "PER_ID":             per_by_acct[a["ACCT_ID"]],
            "CC_TYPE_CD":         _weighted(CC_TYPES, [0.42, 0.18, 0.16, 0.12, 0.12]),
            "CC_DTTM":            _iso_dt(dttm),
            "CC_STATUS_FLG":      _weighted(["50", "10"], [0.94, 0.06]),
            "CC_CL_CD":           "GEN",
            "CONTACT_METH_FLG":   _weighted(CONTACT_METHODS, [0.55, 0.20, 0.15, 0.10]),
            "MESSAGE_SUBJECT":    _weighted([
                "Billing enquiry",
                "Outage report",
                "Start service request",
                "Stop service request",
                "Payment plan enquiry",
            ], [0.42, 0.18, 0.16, 0.12, 0.12]),
            "USER_ID":            _weighted(["CSR01", "CSR02", "CSR03", "IVR", "WEB"], [0.30, 0.25, 0.20, 0.15, 0.10]),
            "MODIFIED_DTTM":      _iso_dt(dttm),
        })
    return ccs


def main() -> None:
    random.seed(SEED)
    fake = Faker("en_US")
    Faker.seed(SEED)

    out = Path(__file__).parent.parent / "data"
    out.mkdir(parents=True, exist_ok=True)

    print("[generate] persons …")
    persons = build_persons(fake)

    print("[generate] premises …")
    premises = build_premises(fake, persons)

    print("[generate] accounts …")
    accounts = build_accounts(persons, premises)

    print("[generate] service_agreements …")
    sas = build_service_agreements(accounts, premises)

    print("[generate] meters …")
    meters = build_meters(fake, sas)

    print("[generate] bills …")
    bills = build_bills(accounts, sas)

    print("[generate] payments …")
    payments = build_payments(accounts, bills)

    print("[generate] customer_contacts …")
    ccs = build_customer_contacts(accounts, persons)

    entity_data = {
        "person":             persons,
        "account":            accounts,
        "premise":            premises,
        "service_agreement":  sas,
        "meter":              meters,
        "bill":               bills,
        "payment":            payments,
        "customer_contact":   ccs,
    }
    for name, rows in entity_data.items():
        (out / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")

    totals = " · ".join(f"{k}={len(v):,}" for k, v in entity_data.items())
    print(f"[done] {totals}")


if __name__ == "__main__":
    main()
