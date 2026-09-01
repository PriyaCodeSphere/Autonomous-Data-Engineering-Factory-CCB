"""Mock Oracle Customer Care & Billing (CC&B) REST API.

Serves the eight core CC&B tables the demo onboards with realistic REST
behaviour: cursor pagination via `modified_since`, page tokens, and simple
bearer-token auth. This is what the Pipeline Configuration Agent's custom
Fivetran connector would call.

Endpoints:
  /v1/persons              — CI_PER
  /v1/accounts             — CI_ACCT
  /v1/premises             — CI_PREM
  /v1/service_agreements   — CI_SA
  /v1/meters               — CI_MTR
  /v1/bills                — CI_BILL
  /v1/payments             — CI_PAY
  /v1/customer_contacts    — CI_CC
  /v1/schema, /v1/health, /v1/

Run:  uvicorn mock_source.main:app --port 8001 --reload
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

DATA_DIR = Path(__file__).parent.parent / "data"
PAGE_SIZE = 500
BEARER_TOKEN = os.getenv("MOCK_SOURCE_TOKEN", "demo-token")

router = APIRouter(tags=["mock-source"])
app = FastAPI(title="Oracle CC&B (mock source)", version="1.0.0")


# --- Entity registry ------------------------------------------------------
# name : (endpoint_path, primary_key, cc&b_table, fields[(name,type)])
ENTITIES: dict[str, dict[str, Any]] = {
    "person": {
        "cc_b_table": "CI_PER",
        "primary_key": ["PER_ID"],
        "fields": [
            ("PER_ID", "string"), ("PER_OR_BUS_FLG", "string"), ("LANGUAGE_CD", "string"),
            ("ADDRESS1", "string"), ("CITY", "string"), ("STATE", "string"),
            ("POSTAL", "string"), ("COUNTRY", "string"), ("EMAILID", "string"),
            ("GEO_CODE", "string"), ("HOUSE_TYPE", "string"), ("IN_CITY_LIMIT", "string"),
            ("LS_SL_FLG", "string"), ("LS_SL_DESCR", "string"),
            ("RECV_MKTG_INFO_FLG", "string"), ("TIME_ZONE_CD", "string"),
            ("MODIFIED_DTTM", "timestamp"),
        ],
    },
    "account": {
        "cc_b_table": "CI_ACCT",
        "primary_key": ["ACCT_ID"],
        "fields": [
            ("ACCT_ID", "string"), ("CIS_DIVISION", "string"), ("CUST_CL_CD", "string"),
            ("COLL_CL_CD", "string"), ("BILL_CYC_CD", "string"), ("CURRENCY_CD", "string"),
            ("SETUP_DT", "date"), ("MAILING_PREM_ID", "string"),
            ("ACCT_MGMT_GRP_CD", "string"), ("PROTECT_CYC_SW", "string"),
            ("NO_DEP_RVW_SW", "string"), ("PER_ID", "string"),
            ("MODIFIED_DTTM", "timestamp"),
        ],
    },
    "premise": {
        "cc_b_table": "CI_PREM",
        "primary_key": ["PREM_ID"],
        "fields": [
            ("PREM_ID", "string"), ("CIS_DIVISION", "string"), ("ADDRESS1", "string"),
            ("CITY", "string"), ("STATE", "string"), ("POSTAL", "string"),
            ("COUNTRY", "string"), ("PREM_TYPE_CD", "string"), ("GEO_CODE", "string"),
            ("OK_TO_ENTER_SW", "string"), ("LS_SL_FLG", "string"),
            ("MR_CYC_CD", "string"), ("TREND_AREA_CD", "string"),
            ("MODIFIED_DTTM", "timestamp"),
        ],
    },
    "service_agreement": {
        "cc_b_table": "CI_SA",
        "primary_key": ["SA_ID"],
        "fields": [
            ("SA_ID", "string"), ("ACCT_ID", "string"), ("SA_TYPE_CD", "string"),
            ("SA_STATUS_FLG", "string"), ("CIS_DIVISION", "string"),
            ("CHAR_PREM_ID", "string"), ("START_DT", "date"), ("END_DT", "date"),
            ("CURRENCY_CD", "string"), ("ALLOW_EST_SW", "string"),
            ("HIGH_BILL_AMT", "number"), ("MODIFIED_DTTM", "timestamp"),
        ],
    },
    "meter": {
        "cc_b_table": "CI_MTR",
        "primary_key": ["MTR_ID"],
        "fields": [
            ("MTR_ID", "string"), ("BADGE_NBR", "string"), ("SERIAL_NBR", "string"),
            ("MTR_TYPE_CD", "string"), ("MTR_STATUS_FLG", "string"),
            ("MFG_CD", "string"), ("MODEL_CD", "string"),
            ("RECEIVE_DT", "date"), ("RETIRE_DT", "date"),
            ("SA_ID", "string"), ("MODIFIED_DTTM", "timestamp"),
        ],
    },
    "bill": {
        "cc_b_table": "CI_BILL",
        "primary_key": ["BILL_ID"],
        "fields": [
            ("BILL_ID", "string"), ("ACCT_ID", "string"), ("BILL_CYC_CD", "string"),
            ("BILL_STAT_FLG", "string"), ("BILL_DT", "date"), ("DUE_DT", "date"),
            ("COMPLETE_DTTM", "timestamp"), ("WIN_START_DT", "date"),
            ("CRE_DTTM", "timestamp"), ("LATE_PAY_CHARGE_SW", "string"),
            ("ALLOW_REOPEN_SW", "string"), ("TOTAL_AMOUNT", "number"),
            ("MODIFIED_DTTM", "timestamp"),
        ],
    },
    "payment": {
        "cc_b_table": "CI_PAY",
        "primary_key": ["PAY_ID"],
        "fields": [
            ("PAY_ID", "string"), ("ACCT_ID", "string"), ("PAY_AMT", "number"),
            ("PAY_STATUS_FLG", "string"), ("MATCH_TYPE_CD", "string"),
            ("MATCH_VAL", "string"), ("CURRENCY_CD", "string"),
            ("PAY_DT", "date"), ("TENDER_TYPE_CD", "string"),
            ("BILL_ID", "string"), ("MODIFIED_DTTM", "timestamp"),
        ],
    },
    "customer_contact": {
        "cc_b_table": "CI_CC",
        "primary_key": ["CC_ID"],
        "fields": [
            ("CC_ID", "string"), ("ACCT_ID", "string"), ("PER_ID", "string"),
            ("CC_TYPE_CD", "string"), ("CC_DTTM", "timestamp"),
            ("CC_STATUS_FLG", "string"), ("CC_CL_CD", "string"),
            ("CONTACT_METH_FLG", "string"), ("MESSAGE_SUBJECT", "string"),
            ("USER_ID", "string"), ("MODIFIED_DTTM", "timestamp"),
        ],
    },
}

CURSOR_FIELD = "MODIFIED_DTTM"


def _load(name: str) -> list[dict[str, Any]]:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(500, f"Missing {path}. Run: python mock_source/generate_data.py")
    return json.loads(path.read_text(encoding="utf-8"))


def _paginate(rows: list[dict], modified_since: str | None, offset: int) -> dict:
    filtered = [r for r in rows if (modified_since is None or (r.get(CURSOR_FIELD) or "") > modified_since)]
    filtered.sort(key=lambda r: r.get(CURSOR_FIELD) or "")
    page = filtered[offset: offset + PAGE_SIZE]
    max_ts = page[-1].get(CURSOR_FIELD) if page else modified_since
    next_offset = offset + PAGE_SIZE if offset + PAGE_SIZE < len(filtered) else None
    return {
        "data": page,
        "count": len(page),
        "total_matched": len(filtered),
        "max_modified_ts": max_ts,
        "next": next_offset,
    }


def _check_auth(authorization: str | None) -> None:
    if authorization != f"Bearer {BEARER_TOKEN}":
        raise HTTPException(401, "Missing or invalid bearer token")


def _entity_endpoint(entity_name: str):
    """Factory for the per-entity GET handlers."""
    async def handler(
        modified_since: str | None = Query(None),
        offset: int = Query(0, ge=0),
        authorization: str | None = Header(None),
    ):
        _check_auth(authorization)
        return JSONResponse(_paginate(_load(entity_name), modified_since, offset))
    handler.__name__ = f"list_{entity_name}"
    return handler


# Register /v1/{entity}s for each of the eight CC&B tables.
for _name in ENTITIES:
    router.add_api_route(f"/v1/{_name}s", _entity_endpoint(_name), methods=["GET"])


@router.get("/v1/")
def mock_root() -> dict:
    """Descriptor for the mock source."""
    return {
        "name": "Oracle CC&B (mock)",
        "endpoints": [f"/v1/{n}s" for n in ENTITIES] + ["/v1/health", "/v1/schema"],
        "auth": "Bearer <token>",
        "pagination": "cursor via `modified_since`, page via `offset`",
    }


@router.get("/v1/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/v1/schema")
def schema() -> dict:
    """Discovery endpoint used by the Pipeline Configuration Agent."""
    return {
        "source": {
            "name": "Oracle Customer Care & Billing",
            "vendor": "Oracle",
            "system_of_record": True,
        },
        "entities": [
            {
                "name": name,
                "cc_b_table": meta["cc_b_table"],
                "primary_key": meta["primary_key"],
                "cursor_field": CURSOR_FIELD,
                "fields": [[fname, ftype] for fname, ftype in meta["fields"]],
            }
            for name, meta in ENTITIES.items()
        ],
    }


# Attach the router to the standalone app (used when running as its own process).
app.include_router(router)
