"""Data Profiling & Validation Agent — pandas, no LLM.

Reads the Oracle CC&B source through the mock's paginated API, computes
per-column statistics, flags anomalies (e.g. overpayments where PAY_AMT
exceeds the billed TOTAL_AMOUNT), and publishes the profile JSON that
downstream agents (DQ, PII, Synth) consume.
"""
from __future__ import annotations

from typing import Any

import httpx
import pandas as pd

from .base import Agent, RunContext


PAGE_SIZE = 500


async def _fetch_all(source_url: str, token: str, entity: str) -> pd.DataFrame:
    headers = {"Authorization": f"Bearer {token}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            r = await client.get(
                f"{source_url}/v1/{entity}s",
                headers=headers,
                params={"offset": offset},
            )
            r.raise_for_status()
            body = r.json()
            rows.extend(body["data"])
            if body.get("next") is None:
                break
            offset = body["next"]
    return pd.DataFrame(rows)


class ProfilerAgent(Agent):
    id = "profile"
    name = "Data Profiling Agent"
    stage = "profile"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)

        # Prefer the pipeline's actual discovered entities; else fall back to the plan.
        entities = ctx.outputs.get("pipeline", {}).get("entities") \
            or [e["name"] for e in ctx.outputs.get("plan", {}).get("entities", [])] \
            or ["person", "account", "bill"]

        profile: dict[str, Any] = {"entities": {}}
        cached: dict[str, pd.DataFrame] = {}

        for entity in entities:
            self.emit(ctx, f"Sampling {entity} via /v1/{entity}s …")
            df = await _fetch_all(ctx.source_url, ctx.source_token, entity)
            cached[entity] = df
            self.emit(ctx, f"loaded {len(df):,} rows · {len(df.columns)} columns", level="ok")

            cols: dict[str, Any] = {}
            for col in df.columns:
                s = df[col]
                null_pct = round((int(s.isna().sum()) / max(len(s), 1)) * 100, 3)
                distinct_pct = round(int(s.nunique(dropna=True)) / max(len(s), 1) * 100, 3)
                col_info: dict[str, Any] = {
                    "dtype": str(s.dtype),
                    "rows": int(len(s)),
                    "null_pct": null_pct,
                    "distinct_pct": distinct_pct,
                }
                ss = s.dropna()
                is_num = pd.api.types.is_numeric_dtype(ss) and not pd.api.types.is_bool_dtype(ss)
                if is_num and len(ss):
                    ss_f = ss.astype(float)
                    col_info.update({
                        "min":    float(ss_f.min()),
                        "max":    float(ss_f.max()),
                        "mean":   float(ss_f.mean()),
                        "median": float(ss_f.median()),
                        "p95":    float(ss_f.quantile(0.95)),
                    })
                else:
                    sample = s.dropna().astype(str).head(5).tolist()
                    col_info["sample"] = sample
                cols[col] = col_info
            profile["entities"][entity] = {
                "row_count": int(len(df)),
                "columns": cols,
            }

        # Cross-entity anomaly: PAY_AMT > TOTAL_AMOUNT (overpayment) joined on BILL_ID
        anomalies: dict[str, int] = {}
        try:
            if "bill" in cached and "payment" in cached:
                bill_df = cached["bill"]
                pay_df = cached["payment"]
                if "BILL_ID" in pay_df.columns and "TOTAL_AMOUNT" in bill_df.columns:
                    merged = pay_df.merge(
                        bill_df[["BILL_ID", "TOTAL_AMOUNT"]], on="BILL_ID", how="left",
                    )
                    overpaid = int((merged["PAY_AMT"] > merged["TOTAL_AMOUNT"]).sum())
                    anomalies["overpayment_amount_over_bill"] = overpaid
                    if overpaid:
                        self.emit(ctx, f"anomaly · PAY_AMT > TOTAL_AMOUNT in {overpaid} rows", level="warn")
        except Exception:  # noqa: BLE001
            pass

        # Single-entity anomaly: bills flagged for late-pay charge (LATE_PAY_CHARGE_SW='Y')
        try:
            if "bill" in cached and "LATE_PAY_CHARGE_SW" in cached["bill"].columns:
                late = int((cached["bill"]["LATE_PAY_CHARGE_SW"] == "Y").sum())
                anomalies["bills_flagged_late"] = late
                if late:
                    self.emit(ctx, f"anomaly · bills flagged LATE_PAY_CHARGE_SW=Y in {late} rows", level="warn")
        except Exception:  # noqa: BLE001
            pass

        # Bills in review status (BILL_STAT_FLG='60')
        try:
            if "bill" in cached and "BILL_STAT_FLG" in cached["bill"].columns:
                in_review = int((cached["bill"]["BILL_STAT_FLG"] == "60").sum())
                anomalies["bills_in_review_high_amount"] = in_review
        except Exception:  # noqa: BLE001
            pass

        profile["anomalies"] = anomalies

        # Histogram of bill TOTAL_AMOUNT for the UI
        try:
            if "bill" in cached and "TOTAL_AMOUNT" in cached["bill"].columns:
                bill_df = cached["bill"]
                bins = [0, 100, 250, 500, 1000, 2500, 10000, 10**9]
                labels = ["$0–$100", "$100–$250", "$250–$500", "$500–$1K", "$1K–$2.5K", "$2.5K–$10K", "$10K+"]
                cats = pd.cut(bill_df["TOTAL_AMOUNT"], bins=bins, labels=labels, right=False)
                hist = cats.value_counts().reindex(labels, fill_value=0)
                profile["bill_amount_hist"] = {
                    "labels": labels,
                    "counts": [int(v) for v in hist.values],
                    "pct":    [round(int(v) / max(len(bill_df), 1) * 100, 2) for v in hist.values],
                }
        except Exception:  # noqa: BLE001
            pass

        p = ctx.write_json(("profile", "profile.json"), profile)
        self.artifact(ctx, "profile.json", p, preview="")
        ctx.outputs["profile"] = profile
        total_rows = sum(v["row_count"] for v in profile["entities"].values())
        self.done(ctx, f"Profiled {total_rows:,} rows across {len(profile['entities'])} entities")
        return profile
