"""Data Democratization & Consumption Agent — Cognizant reusable pattern.

After the data product is deployed, this agent makes it consumable by
business users:

- Publishes the certified data product to the Enterprise Data Catalog
  (Atlan-style), including business glossary linkage
- Provisions role-based access packs
- Generates natural-language query examples with LLM
- Emits a business-friendly quickstart guide with LLM
- Registers the semantic layer / metric definitions
"""
from __future__ import annotations

from .base import Agent, RunContext
from .. import llm


QUERY_EXAMPLES_SYSTEM = """You are the Data Democratization Agent. You are asked
to help a business analyst who has never used the Customer 360 data product at
Con Edison before. Produce 4 realistic natural-language questions they might
ask, along with the SQL each maps to (Snowflake dialect).

Available marts:
  dim_customer_360(acct_id, per_id, email_hash, mailing_address_hash,
                   service_city, service_state, service_postal_code,
                   customer_class, collection_class, bill_cycle, setup_dt,
                   cis_division)
  dim_meter(mtr_id, sa_id, acct_id, meter_type, meter_status, manufacturer,
            model, receive_dt, retire_dt, is_active)
  fct_bill(bill_id, acct_id, bill_cycle, bill_status, bill_dt, due_dt,
           bill_period_start, late_pay_charge_sw, total_amount,
           bill_days, bill_state)
  fct_payment(pay_id, acct_id, bill_id, pay_amount, billed_amount,
              amount_variance, pay_status, pay_dt, tender_type,
              is_overpayment)

Return strict JSON:
  {"examples": [{"question": "...", "sql": "...", "why": "one-sentence rationale"}]}
Keep SQL under ~6 lines each, readable, no fancy CTEs unless needed.
"""

QUICKSTART_SYSTEM = """You are the Data Democratization Agent. Write a friendly,
one-page 'How to use this data product' quickstart in markdown for a business
analyst who is new to the Con Edison Customer 360 certified dataset (built
from Oracle CC&B). Cover:
1. What the data represents (2-3 sentences)
2. How to request access (one paragraph)
3. Key metrics available (bulleted list of 4-6 items)
4. Common questions this data can answer (bulleted list of 4-5 items)
5. Who to contact for help
Keep the total under ~35 lines. Return only markdown."""


class DemocratizationAgent(Agent):
    id = "democratize"
    name = "Data Democratization Agent"
    stage = "democratize"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        req = ctx.request

        self.emit(ctx, "Publishing to Enterprise Data Catalog (Atlan)…")
        catalog_entry = _atlan_entry(req)
        p = ctx.write_json(("democratize", "atlan_asset.json"), catalog_entry)
        self.artifact(ctx, "atlan_asset.json", p, preview="")
        self.emit(ctx, "asset published · marked 'Certified' · owner assigned", level="ok")

        glossary = _glossary_map()
        p = ctx.write_json(("democratize", "glossary_links.json"), glossary)
        self.artifact(ctx, "glossary_links.json", p, preview="")
        self.emit(ctx, f"linked {len(glossary['links'])} columns to business glossary terms", level="ok")

        self.emit(ctx, "Provisioning role-based access packs…")
        access = _access_packs()
        p = ctx.write_json(("democratize", "access_packs.json"), access)
        self.artifact(ctx, "access_packs.json", p, preview="")
        self.emit(ctx, f"{len(access['packs'])} access packs created; auto-approval routing configured", level="ok")

        self.emit(ctx, "Generating natural-language query examples with LLM…")
        result = llm.complete_json(QUERY_EXAMPLES_SYSTEM,
                                   "Generate query examples now.", temperature=0.3,
                                   max_tokens=1500)
        examples = result.get("examples") or _fallback_examples()
        p = ctx.write_json(("democratize", "query_examples.json"), {"examples": examples})
        self.artifact(ctx, "query_examples.json", p, preview="")

        semantic = _semantic_layer()
        p = ctx.write_text(("democratize", "metrics.yml"), semantic)
        self.artifact(ctx, "metrics.yml", p, preview=semantic)

        self.emit(ctx, "Writing 'How to use this data' quickstart…")
        quickstart = llm.complete(QUICKSTART_SYSTEM,
                                  f"Source: {req.get('source_name','Oracle CC&B')}. "
                                  "Business owner: Customer Operations COE. "
                                  "Steward: shanmugapriya.kandasamy@cognizant.com.",
                                  temperature=0.4, max_tokens=1000)
        if not quickstart:
            quickstart = _fallback_quickstart()
        p = ctx.write_text(("democratize", "QUICKSTART.md"), quickstart)
        self.artifact(ctx, "QUICKSTART.md", p, preview=quickstart)

        ctx.outputs["democratize"] = {
            "catalog_asset": catalog_entry["asset_id"],
            "glossary_links": len(glossary["links"]),
            "access_packs": len(access["packs"]),
            "query_examples": len(examples),
            "metrics": semantic.count("- name:"),
            "examples": examples,
            "glossary": glossary,
            "access": access,
        }
        self.done(ctx, "Data product democratized · available to business users")
        return ctx.outputs["democratize"]


def _atlan_entry(req: dict) -> dict:
    return {
        "asset_id": "atlan://customer-360/certified",
        "name": "Customer 360 — Certified",
        "type": "Data Product",
        "certification": "Certified",
        "owner": "Customer Operations COE",
        "steward": "shanmugapriya.kandasamy@cognizant.com",
        "tags": ["ccb", "customer-360", "billing", "certified", "PII-safe"],
        "sla": {"refresh": req.get("refresh", "30 min"), "lag_alert_minutes": 15},
        "tables": [
            {"name": "dim_customer_360", "row_count": 1000, "certified": True},
            {"name": "dim_meter",        "row_count": 1000, "certified": True},
            {"name": "fct_bill",         "row_count": 3000, "certified": True},
            {"name": "fct_payment",      "row_count": 2500, "certified": True},
        ],
        "connections": {
            "warehouse": "snowflake://DP_PROD.CCB.*",
            "bi":        "powerbi://Customer 360 — Certified",
            "reverse_etl": "hightouch://billing_alerts_v2",
        },
        "quality_score": 98,
        "popularity_rank": None,
    }


def _glossary_map() -> dict:
    return {
        "links": [
            {"column": "fct_bill.total_amount",     "term": "Billed Amount",       "definition": "Total invoiced amount for the bill cycle, in USD."},
            {"column": "fct_bill.bill_state",       "term": "Bill State",          "definition": "Derived lifecycle: complete, past_due, in_review, cancelled."},
            {"column": "fct_payment.pay_amount",    "term": "Payment Amount",      "definition": "Dollar amount posted for a payment transaction."},
            {"column": "fct_payment.is_overpayment","term": "Overpayment Flag",    "definition": "True when a payment exceeds the joined bill total."},
            {"column": "dim_customer_360.customer_class", "term": "Customer Class","definition": "RES (residential), COM (commercial), or IND (industrial)."},
            {"column": "dim_meter.is_active",       "term": "Active Meter",        "definition": "Meter is not retired and status flag is AC."},
        ],
    }


def _access_packs() -> dict:
    return {
        "packs": [
            {"name": "customer_360_read",    "grants": "SELECT on DP_PROD.CCB.*",              "audience": "Customer Ops analysts", "auto_approve": True},
            {"name": "customer_360_finance", "grants": "SELECT + financial columns unmasked", "audience": "Finance team",           "auto_approve": False},
            {"name": "customer_360_pii",     "grants": "SELECT + PII columns unmasked",       "audience": "Steward-approved only",  "auto_approve": False},
            {"name": "customer_360_bi",      "grants": "Power BI dataset access, no direct SQL", "audience": "Business users",     "auto_approve": True},
        ],
        "request_flow": "self-serve via ServiceNow → auto-approved for _read/_bi; steward-approval for _finance/_pii",
    }


def _semantic_layer() -> str:
    return """# semantic/metrics/customer_360.yml
version: 2
metrics:
  - name: total_billed_revenue
    label: "Total Billed Revenue"
    model: ref('fct_bill')
    calculation_method: sum
    expression: total_amount
    filters: [{field: bill_state, operator: 'in', value: "'complete','past_due'"}]

  - name: bill_count
    label: "Bills Issued"
    model: ref('fct_bill')
    calculation_method: count
    expression: bill_id

  - name: average_bill
    label: "Average Bill Amount"
    model: ref('fct_bill')
    calculation_method: derived
    expression: metric('total_billed_revenue') / metric('bill_count')

  - name: overpayment_rate
    label: "Overpayment Rate"
    model: ref('fct_payment')
    calculation_method: derived
    expression: sum(case when is_overpayment then 1 else 0 end) / count(pay_id)

  - name: active_customers
    label: "Active Customers"
    model: ref('dim_customer_360')
    calculation_method: count_distinct
    expression: acct_id
"""


def _fallback_examples() -> list[dict]:
    return [
        {"question": "What was total billed revenue last month by service borough (CIS division)?",
         "sql": "SELECT c.cis_division, SUM(b.total_amount) AS revenue "
                "FROM fct_bill b JOIN dim_customer_360 c USING(acct_id) "
                "WHERE b.bill_dt >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month' "
                "AND b.bill_dt < DATE_TRUNC('month', CURRENT_DATE) "
                "GROUP BY 1 ORDER BY revenue DESC;",
         "why": "Territory-level revenue rollup for the most recent full month."},
        {"question": "Which accounts had the highest overpayment rate this quarter?",
         "sql": "SELECT acct_id, "
                "SUM(CASE WHEN is_overpayment THEN 1 ELSE 0 END)::FLOAT / COUNT(*) AS overpay_rate "
                "FROM fct_payment WHERE pay_dt >= DATE_TRUNC('quarter', CURRENT_DATE) "
                "GROUP BY 1 HAVING COUNT(*) >= 3 ORDER BY overpay_rate DESC LIMIT 20;",
         "why": "Credit-management signal — accounts with repeat overpayments."},
        {"question": "Top 10 residential accounts by lifetime billed amount?",
         "sql": "SELECT c.acct_id, SUM(b.total_amount) AS lifetime_billed "
                "FROM fct_bill b JOIN dim_customer_360 c USING(acct_id) "
                "WHERE c.customer_class = 'RES' "
                "GROUP BY 1 ORDER BY lifetime_billed DESC LIMIT 10;",
         "why": "Highest-value residential customers for retention outreach."},
        {"question": "How does average bill differ between residential and commercial customers?",
         "sql": "SELECT c.customer_class, AVG(b.total_amount) AS avg_bill "
                "FROM fct_bill b JOIN dim_customer_360 c USING(acct_id) "
                "GROUP BY 1 ORDER BY avg_bill DESC;",
         "why": "Segment-level pricing baseline."},
    ]


def _fallback_quickstart() -> str:
    return """# Customer 360 — Quickstart

This data product contains the certified Customer 360 dataset for Con Edison —
persons, accounts, service premises, meters, bills and payments — refreshed
every 30 minutes from Oracle CC&B. It's governed, PII-masked, and safe for
broad analyst use.

## Requesting access
Access is self-serve via ServiceNow. Search for **customer_360_read** and
click Request. You'll receive Snowflake and Power BI grants automatically —
usually within 5 minutes. For finance or PII-unmasked access, the steward
reviews.

## Key metrics
- **Total Billed Revenue** — sum of `total_amount` for complete/past-due bills
- **Bill Count** — count of `bill_id` for the period
- **Average Bill** — total billed revenue ÷ bill count
- **Overpayment Rate** — payments with `is_overpayment = true` ÷ total payments
- **Active Customers** — distinct `acct_id` in the period
- **Active Meters** — meters with `is_active = true`

## Common questions this data can answer
- Which boroughs (CIS divisions) are driving revenue this month?
- Which accounts have the highest overpayment rate this quarter?
- Who are our top-10 residential customers by lifetime billed amount?
- How does average bill differ between RES / COM / IND customer classes?
- Are there seasonality patterns in bill amounts?

## Contact
- Business owner: **Customer Operations COE** — customer_ops_coe@coned.com
- Steward: **shanmugapriya.kandasamy@cognizant.com**
- On-call: **#customer-ops-oncall** Slack channel
"""
