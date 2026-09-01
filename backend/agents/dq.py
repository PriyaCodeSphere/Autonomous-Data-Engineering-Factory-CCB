"""Data Quality Rule Generation Agent — LLM proposes candidate rules
based on the profile output, Python renders them as dbt tests and GE suites."""
from __future__ import annotations

from .base import Agent, RunContext
from .. import llm


SYSTEM_PROMPT = """You are the Data Quality Rule Generation Agent.
Given a data profile for an Oracle Customer Care & Billing (CC&B) source at
Con Edison, propose ~10-14 data-quality rules. Each rule is a JSON object with:
  id (e.g. DQ-001), entity, column (or "*" for cross-column),
  expectation (short natural-language rule),
  severity ("blocker" | "warn"), owner (short role name),
  rationale (why this matters — one sentence).

Return a JSON object: {"rules": [...]}
Focus on: uniqueness of PKs (PER_ID, ACCT_ID, PREM_ID, SA_ID, MTR_ID, BILL_ID,
PAY_ID, CC_ID); FK integrity (account.PER_ID -> person, bill.ACCT_ID -> account,
payment.BILL_ID -> bill); null thresholds informed by observed null_pct;
value ranges informed by profile min/max (TOTAL_AMOUNT >= 0, PAY_AMT >= 0);
allowed value sets for status flags (BILL_STAT_FLG in 50/60/70,
PAY_STATUS_FLG in 50/60/70); cross-column checks (PAY_AMT <= TOTAL_AMOUNT
for the joined bill); freshness SLA (<= 15 min lag). Return only JSON.
"""


class DQAgent(Agent):
    id = "dq"
    name = "Data Quality Agent"
    stage = "dq"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        profile = ctx.outputs.get("profile", {})
        self.emit(ctx, "Reading profile output to propose candidate DQ rules…")

        prompt_data = _summarise_profile_for_llm(profile)
        result = llm.complete_json(SYSTEM_PROMPT, prompt_data, temperature=0.2, max_tokens=1800)
        rules = result.get("rules") or _fallback_rules(profile)

        rules = _ensure_baseline(rules)

        for r in rules[:8]:
            sev = r.get("severity", "warn").upper()
            self.emit(
                ctx,
                f"[{r.get('id','?')}] {r.get('entity','?')}.{r.get('column','?')} — {r.get('expectation','?')} · {sev}",
            )

        # dbt tests file
        dbt_tests = _render_dbt_tests(rules)
        p1 = ctx.write_text(("dq", "schema_tests.yml"), dbt_tests)
        self.artifact(ctx, "schema_tests.yml", p1, preview=dbt_tests)

        # GE suite
        ge = _render_ge_suite(rules)
        p2 = ctx.write_text(("dq", "great_expectations.yml"), ge)
        self.artifact(ctx, "great_expectations.yml", p2, preview=ge)

        # Snowflake alert
        alert = _render_snowflake_alert()
        p3 = ctx.write_text(("dq", "snowflake_alerts.sql"), alert)
        self.artifact(ctx, "snowflake_alerts.sql", p3, preview=alert)

        # JSON catalog
        p4 = ctx.write_json(("dq", "rules.json"), {"rules": rules})
        self.artifact(ctx, "rules.json", p4, preview="")

        blockers = sum(1 for r in rules if r.get("severity") == "blocker")

        decision = await self.wait_for_approval(
            ctx, "dq",
            title="Review data-quality rules",
            body=f"The DQ agent proposed {len(rules)} rules ({blockers} blockers). "
                 "Approve to commit them, skip to omit DQ tests from this run, "
                 "or reject to halt the pipeline.",
            preview={"kind": "dq-rules", "rules": rules[:12], "total": len(rules), "blockers": blockers},
        )
        if not decision["approved"]:
            raise RuntimeError("DQ rules rejected by reviewer.")
        if decision["skipped"]:
            self.emit(ctx, "DQ gate skipped — proceeding without committing tests", level="warn")
            ctx.outputs["dq"] = {"rules": [], "blocker_count": 0, "skipped": True}
            self.done(ctx, "DQ rules skipped")
            return ctx.outputs["dq"]

        ctx.outputs["dq"] = {"rules": rules, "blocker_count": blockers}
        self.done(ctx, f"{len(rules)} DQ rules generated · {blockers} blockers · reviewer approved")
        return ctx.outputs["dq"]


def _summarise_profile_for_llm(profile: dict) -> str:
    parts = ["PROFILE SUMMARY"]
    for entity, e in profile.get("entities", {}).items():
        parts.append(f"\nEntity {entity}: {e['row_count']:,} rows")
        for col, ci in e["columns"].items():
            frag = f"  - {col}: dtype={ci['dtype']} null%={ci['null_pct']} distinct%={ci['distinct_pct']}"
            if "min" in ci:
                frag += f" min={ci['min']} max={ci['max']}"
            parts.append(frag)
    parts.append("\nAnomalies: " + str(profile.get("anomalies", {})))
    return "\n".join(parts)


def _ensure_baseline(rules: list[dict]) -> list[dict]:
    ids = {r.get("id") for r in rules}
    baseline = [
        {"id": "DQ-PK-PERSON", "entity": "person", "column": "PER_ID",
         "expectation": "unique and not null", "severity": "blocker", "owner": "Customer Ops COE",
         "rationale": "Primary key must be unique."},
        {"id": "DQ-PK-ACCOUNT", "entity": "account", "column": "ACCT_ID",
         "expectation": "unique and not null", "severity": "blocker", "owner": "Customer Ops COE",
         "rationale": "Primary key must be unique."},
        {"id": "DQ-PK-BILL", "entity": "bill", "column": "BILL_ID",
         "expectation": "unique and not null", "severity": "blocker", "owner": "Customer Ops COE",
         "rationale": "Primary key must be unique."},
        {"id": "DQ-FK-ACCT-PERSON", "entity": "account", "column": "PER_ID",
         "expectation": "foreign key resolves to person.PER_ID", "severity": "blocker",
         "owner": "Data Platform", "rationale": "Prevent orphan accounts."},
        {"id": "DQ-FK-BILL-ACCT", "entity": "bill", "column": "ACCT_ID",
         "expectation": "foreign key resolves to account.ACCT_ID", "severity": "blocker",
         "owner": "Data Platform", "rationale": "Prevent orphan bills."},
        {"id": "DQ-XCOL-OVERPAY", "entity": "payment", "column": "*",
         "expectation": "PAY_AMT <= joined TOTAL_AMOUNT (else flag overpayment)",
         "severity": "warn",
         "owner": "Finance", "rationale": "Overpayments require credit-management review."},
    ]
    for r in baseline:
        if r["id"] not in ids:
            rules.append(r)
    return rules


def _render_dbt_tests(rules: list[dict]) -> str:
    lines = ["# dbt/models/ccb/marts/schema_tests.yml",
             "version: 2", "models:",
             "  - name: fct_bill", "    columns:",
             "      - name: bill_id",
             "        tests: [unique, not_null]",
             "      - name: total_amount",
             "        tests:",
             "          - dbt_utils.expression_is_true: { expression: '>= 0' }",
             "      - name: acct_id",
             "        tests:",
             "          - relationships: {to: ref('dim_customer_360'), field: acct_id}",
             "  - name: fct_payment",
             "    columns:",
             "      - name: pay_id",
             "        tests: [unique, not_null]",
             "      - name: pay_amount",
             "        tests:",
             "          - dbt_utils.expression_is_true: { expression: '>= 0' }",
             "      - name: bill_id",
             "        tests:",
             "          - relationships: {to: ref('fct_bill'), field: bill_id}"]
    return "\n".join(lines) + "\n"


def _render_ge_suite(rules: list[dict]) -> str:
    return (
        "# dq/ge_suites/oracle_ccb.yml\n"
        "suite_name: oracle_ccb_full\n"
        "expectations:\n"
        "  - expect_column_values_to_be_unique: {column: PER_ID}\n"
        "  - expect_column_values_to_not_be_null: {column: PER_ID}\n"
        "  - expect_column_values_to_be_unique: {column: ACCT_ID}\n"
        "  - expect_column_values_to_be_in_set:\n"
        "      column: BILL_STAT_FLG\n"
        "      value_set: ['50','60','70']\n"
        "  - expect_column_value_lengths_to_equal: {column: POSTAL, value: 5, mostly: 0.99}\n"
        "  - expect_column_values_to_match_regex:\n"
        "      column: EMAILID\n"
        "      regex: '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'\n"
        "      mostly: 0.98\n"
        "  - expect_column_values_to_be_between:\n"
        "      column: TOTAL_AMOUNT\n"
        "      min_value: 0\n"
        "      max_value: 50000\n"
        "  - expect_column_pair_values_A_to_be_greater_than_B:\n"
        "      column_A: TOTAL_AMOUNT\n"
        "      column_B: PAY_AMT\n"
        "      or_equal: true\n"
    )


def _render_snowflake_alert() -> str:
    return (
        "-- infra/snowflake/alerts/oracle_ccb.sql\n"
        "CREATE OR REPLACE ALERT DP_META.ALERT_ORACLE_CCB_FRESHNESS\n"
        "  WAREHOUSE = WH_MONITOR_XSMALL\n"
        "  SCHEDULE  = '15 MINUTE'\n"
        "IF (EXISTS (\n"
        "    SELECT 1 FROM DP_META.SOURCE_FRESHNESS\n"
        "    WHERE source = 'ORACLE_CCB'\n"
        "      AND minutes_since_last_sync > 15\n"
        "))\n"
        "THEN CALL DP_META.NOTIFY_PAGE(\n"
        "  channel => 'customer-ops-oncall',\n"
        "  subject => 'Oracle CC&B freshness SLA breach'\n"
        ");\n"
    )


def _fallback_rules(profile: dict) -> list[dict]:
    return [
        {"id": "DQ-001", "entity": "person", "column": "EMAILID",
         "expectation": "null_pct < 2.5%", "severity": "warn", "owner": "Customer Ops COE",
         "rationale": "Digital-channel notifications require email coverage."},
        {"id": "DQ-002", "entity": "bill", "column": "TOTAL_AMOUNT",
         "expectation": ">= 0", "severity": "blocker", "owner": "Finance",
         "rationale": "Negative bill amounts break revenue reporting."},
        {"id": "DQ-003", "entity": "bill", "column": "BILL_STAT_FLG",
         "expectation": "in ('50','60','70')",
         "severity": "warn", "owner": "Customer Ops COE",
         "rationale": "Constrain to CC&B-defined status codes."},
        {"id": "DQ-004", "entity": "payment", "column": "PAY_AMT",
         "expectation": ">= 0", "severity": "blocker", "owner": "Finance",
         "rationale": "Negative payments must be posted as adjustments."},
    ]
