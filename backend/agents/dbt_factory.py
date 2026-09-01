"""dbt Macro Factory Agent — deterministic templates + optional LLM descriptions.

Emits a compact dbt project for the Oracle CC&B onboarding: one staging model
per source entity, four conformed marts, and a reusable PII-hash macro.
"""
from __future__ import annotations

from .base import Agent, RunContext
from .. import llm


# ---------------------------------------------------------------------------
# sources.yml — all eight CC&B tables under one source
# ---------------------------------------------------------------------------
SOURCES_YML = """# dbt/models/ccb/sources.yml
version: 2
sources:
  - name: oracle_ccb
    database: DP_RAW
    schema: RAW_CCB
    loader: fivetran
    loaded_at_field: _fivetran_synced
    freshness:
      warn_after:  {count: 45, period: minute}
      error_after: {count: 90, period: minute}
    tables:
      - name: person
        columns:
          - name: PER_ID
            tests: [unique, not_null]
          - name: EMAILID
            meta: {pii: true, classification: restricted}
          - name: ADDRESS1
            meta: {pii: true, classification: restricted}
      - name: account
        columns:
          - name: ACCT_ID
            tests: [unique, not_null]
          - name: PER_ID
            tests:
              - relationships: {to: source('oracle_ccb','person'), field: PER_ID}
          - name: MAILING_PREM_ID
            tests:
              - relationships: {to: source('oracle_ccb','premise'), field: PREM_ID}
      - name: premise
        columns:
          - name: PREM_ID
            tests: [unique, not_null]
      - name: service_agreement
        columns:
          - name: SA_ID
            tests: [unique, not_null]
          - name: ACCT_ID
            tests:
              - relationships: {to: source('oracle_ccb','account'), field: ACCT_ID}
      - name: meter
        columns:
          - name: MTR_ID
            tests: [unique, not_null]
      - name: bill
        columns:
          - name: BILL_ID
            tests: [unique, not_null]
          - name: ACCT_ID
            tests:
              - relationships: {to: source('oracle_ccb','account'), field: ACCT_ID}
      - name: payment
        columns:
          - name: PAY_ID
            tests: [unique, not_null]
          - name: ACCT_ID
            tests:
              - relationships: {to: source('oracle_ccb','account'), field: ACCT_ID}
      - name: customer_contact
        columns:
          - name: CC_ID
            tests: [unique, not_null]
"""


# ---------------------------------------------------------------------------
# Staging models — one per source entity, mechanical select-and-rename
# ---------------------------------------------------------------------------
def _stg_person() -> str:
    return """-- dbt/models/ccb/staging/stg_ccb__person.sql
{{ config(
    materialized='incremental',
    unique_key='per_id',
    on_schema_change='append_new_columns',
    tags=['ccb','staging','pii']
) }}

with src as (
    select * from {{ source('oracle_ccb','person') }}
    {% if is_incremental() %}
      where MODIFIED_DTTM > (select coalesce(max(modified_dttm),'1900-01-01') from {{ this }})
    {% endif %}
)
select
    PER_ID                              as per_id,
    PER_OR_BUS_FLG                      as person_or_business_flag,
    LANGUAGE_CD                         as language_cd,
    {{ hash_pii('ADDRESS1') }}          as address1_hash,
    CITY                                as city,
    STATE                               as state,
    POSTAL                              as postal_code,
    COUNTRY                             as country,
    {{ hash_pii('EMAILID') }}           as email_hash,
    HOUSE_TYPE                          as house_type,
    RECV_MKTG_INFO_FLG                  as marketing_opt_in_sw,
    MODIFIED_DTTM                       as modified_dttm,
    current_timestamp()                 as _stg_loaded_at
from src
"""


def _stg_account() -> str:
    return """-- dbt/models/ccb/staging/stg_ccb__account.sql
{{ config(materialized='incremental', unique_key='acct_id',
          tags=['ccb','staging']) }}

with src as (
    select * from {{ source('oracle_ccb','account') }}
    {% if is_incremental() %}
      where MODIFIED_DTTM > (select coalesce(max(modified_dttm),'1900-01-01') from {{ this }})
    {% endif %}
)
select
    ACCT_ID          as acct_id,
    PER_ID           as per_id,
    CIS_DIVISION     as cis_division,
    CUST_CL_CD       as customer_class,
    COLL_CL_CD       as collection_class,
    BILL_CYC_CD      as bill_cycle,
    CURRENCY_CD      as currency_cd,
    SETUP_DT         as setup_dt,
    MAILING_PREM_ID  as mailing_prem_id,
    MODIFIED_DTTM    as modified_dttm
from src
"""


def _stg_premise() -> str:
    return """-- dbt/models/ccb/staging/stg_ccb__premise.sql
{{ config(materialized='table', tags=['ccb','staging','pii']) }}

select
    PREM_ID                        as prem_id,
    CIS_DIVISION                   as cis_division,
    {{ hash_pii('ADDRESS1') }}     as address1_hash,
    CITY                           as city,
    STATE                          as state,
    POSTAL                         as postal_code,
    PREM_TYPE_CD                   as premise_type,
    OK_TO_ENTER_SW                 as ok_to_enter_sw,
    MR_CYC_CD                      as meter_read_cycle,
    TREND_AREA_CD                  as trend_area,
    MODIFIED_DTTM                  as modified_dttm
from {{ source('oracle_ccb','premise') }}
"""


def _stg_service_agreement() -> str:
    return """-- dbt/models/ccb/staging/stg_ccb__service_agreement.sql
{{ config(materialized='incremental', unique_key='sa_id',
          tags=['ccb','staging']) }}

with src as (
    select * from {{ source('oracle_ccb','service_agreement') }}
    {% if is_incremental() %}
      where MODIFIED_DTTM > (select coalesce(max(modified_dttm),'1900-01-01') from {{ this }})
    {% endif %}
)
select
    SA_ID           as sa_id,
    ACCT_ID         as acct_id,
    SA_TYPE_CD      as sa_type,
    SA_STATUS_FLG   as sa_status,
    CHAR_PREM_ID    as prem_id,
    START_DT        as start_dt,
    END_DT          as end_dt,
    HIGH_BILL_AMT   as high_bill_amt,
    MODIFIED_DTTM   as modified_dttm
from src
"""


def _stg_meter() -> str:
    return """-- dbt/models/ccb/staging/stg_ccb__meter.sql
{{ config(materialized='table', tags=['ccb','staging']) }}

select
    MTR_ID         as mtr_id,
    SA_ID          as sa_id,
    BADGE_NBR      as badge_nbr,
    SERIAL_NBR     as serial_nbr,
    MTR_TYPE_CD    as meter_type,
    MTR_STATUS_FLG as meter_status,
    MFG_CD         as manufacturer,
    MODEL_CD       as model,
    RECEIVE_DT     as receive_dt,
    RETIRE_DT      as retire_dt,
    MODIFIED_DTTM  as modified_dttm
from {{ source('oracle_ccb','meter') }}
"""


def _stg_bill() -> str:
    return """-- dbt/models/ccb/staging/stg_ccb__bill.sql
{{ config(materialized='incremental', unique_key='bill_id',
          tags=['ccb','staging','financial']) }}

with src as (
    select * from {{ source('oracle_ccb','bill') }}
    {% if is_incremental() %}
      where MODIFIED_DTTM > (select coalesce(max(modified_dttm),'1900-01-01') from {{ this }})
    {% endif %}
)
select
    BILL_ID              as bill_id,
    ACCT_ID              as acct_id,
    BILL_CYC_CD          as bill_cycle,
    BILL_STAT_FLG        as bill_status,
    BILL_DT              as bill_dt,
    DUE_DT               as due_dt,
    WIN_START_DT         as bill_period_start,
    LATE_PAY_CHARGE_SW   as late_pay_charge_sw,
    TOTAL_AMOUNT         as total_amount,
    CRE_DTTM             as created_dttm,
    MODIFIED_DTTM        as modified_dttm
from src
"""


def _stg_payment() -> str:
    return """-- dbt/models/ccb/staging/stg_ccb__payment.sql
{{ config(materialized='incremental', unique_key='pay_id',
          tags=['ccb','staging','financial']) }}

with src as (
    select * from {{ source('oracle_ccb','payment') }}
    {% if is_incremental() %}
      where MODIFIED_DTTM > (select coalesce(max(modified_dttm),'1900-01-01') from {{ this }})
    {% endif %}
)
select
    PAY_ID          as pay_id,
    ACCT_ID         as acct_id,
    BILL_ID         as bill_id,
    PAY_AMT         as pay_amount,
    PAY_STATUS_FLG  as pay_status,
    PAY_DT          as pay_dt,
    TENDER_TYPE_CD  as tender_type,
    MODIFIED_DTTM   as modified_dttm
from src
"""


def _stg_customer_contact() -> str:
    return """-- dbt/models/ccb/staging/stg_ccb__customer_contact.sql
{{ config(materialized='incremental', unique_key='cc_id',
          tags=['ccb','staging']) }}

with src as (
    select * from {{ source('oracle_ccb','customer_contact') }}
    {% if is_incremental() %}
      where MODIFIED_DTTM > (select coalesce(max(modified_dttm),'1900-01-01') from {{ this }})
    {% endif %}
)
select
    CC_ID              as cc_id,
    ACCT_ID            as acct_id,
    PER_ID             as per_id,
    CC_TYPE_CD         as contact_type,
    CC_DTTM            as contact_dttm,
    CC_STATUS_FLG      as contact_status,
    CONTACT_METH_FLG   as contact_channel,
    MESSAGE_SUBJECT    as message_subject,
    USER_ID            as user_id,
    MODIFIED_DTTM      as modified_dttm
from src
"""


STAGING = {
    "person":            _stg_person(),
    "account":           _stg_account(),
    "premise":           _stg_premise(),
    "service_agreement": _stg_service_agreement(),
    "meter":             _stg_meter(),
    "bill":              _stg_bill(),
    "payment":           _stg_payment(),
    "customer_contact":  _stg_customer_contact(),
}


# ---------------------------------------------------------------------------
# Marts
# ---------------------------------------------------------------------------
DIM_CUSTOMER_360 = """-- dbt/models/ccb/marts/dim_customer_360.sql
{{ config(materialized='table', cluster_by=['acct_id'],
          tags=['ccb','marts','customer']) }}

with p as (select * from {{ ref('stg_ccb__person') }}),
     a as (select * from {{ ref('stg_ccb__account') }}),
     m as (select * from {{ ref('stg_ccb__premise') }})
select
    a.acct_id,
    a.per_id,
    p.email_hash,
    p.address1_hash                  as mailing_address_hash,
    m.city                           as service_city,
    m.state                          as service_state,
    m.postal_code                    as service_postal_code,
    a.customer_class,
    a.collection_class,
    a.bill_cycle,
    a.setup_dt,
    a.cis_division,
    p.language_cd,
    p.marketing_opt_in_sw,
    greatest(a.modified_dttm, p.modified_dttm, m.modified_dttm) as modified_dttm
from a
left join p using (per_id)
left join m on m.prem_id = a.mailing_prem_id
"""

DIM_METER = """-- dbt/models/ccb/marts/dim_meter.sql
{{ config(materialized='table', tags=['ccb','marts']) }}

select
    m.mtr_id,
    m.sa_id,
    sa.acct_id,
    m.meter_type,
    m.meter_status,
    m.manufacturer,
    m.model,
    m.receive_dt,
    m.retire_dt,
    (m.retire_dt is null and m.meter_status = 'AC') as is_active
from {{ ref('stg_ccb__meter') }}       m
left join {{ ref('stg_ccb__service_agreement') }} sa using (sa_id)
"""

FCT_BILL = """-- dbt/models/ccb/marts/fct_bill.sql
{{ config(materialized='incremental', unique_key='bill_id',
          tags=['ccb','marts','fact','financial']) }}

with b as (select * from {{ ref('stg_ccb__bill') }}),
     a as (select acct_id from {{ ref('stg_ccb__account') }})
select
    b.bill_id,
    b.acct_id,
    b.bill_cycle,
    b.bill_status,
    b.bill_dt,
    b.due_dt,
    b.bill_period_start,
    b.late_pay_charge_sw,
    b.total_amount,
    datediff('day', b.bill_period_start, b.bill_dt)   as bill_days,
    case when b.bill_status = '60' then 'in_review'
         when b.late_pay_charge_sw = 'Y' then 'past_due'
         when b.bill_status = '70' then 'cancelled'
         else 'complete' end                          as bill_state,
    b.modified_dttm
from b
inner join a using (acct_id)
"""

FCT_PAYMENT = """-- dbt/models/ccb/marts/fct_payment.sql
{{ config(materialized='incremental', unique_key='pay_id',
          tags=['ccb','marts','fact','financial']) }}

with p as (select * from {{ ref('stg_ccb__payment') }}),
     b as (select bill_id, total_amount from {{ ref('stg_ccb__bill') }})
select
    p.pay_id,
    p.acct_id,
    p.bill_id,
    p.pay_amount,
    b.total_amount               as billed_amount,
    p.pay_amount - b.total_amount as amount_variance,
    p.pay_status,
    p.pay_dt,
    p.tender_type,
    (p.pay_amount > b.total_amount) as is_overpayment,
    p.modified_dttm
from p
left join b using (bill_id)
"""

MACRO_HASH = """-- dbt/macros/hash_pii.sql (Cognizant reusable macro)
{% macro hash_pii(col, salt_ref='enterprise_pii_salt') %}
  case
    when {{ col }} is null then null
    else sha2( concat({{ col }}, {{ var(salt_ref) }}), 256 )
  end
{% endmacro %}
"""


SYSTEM_PROMPT = """You are the Documentation half of the dbt Macro Factory Agent.
Given the source is Oracle CC&B (customer/account/premise/service_agreement/
meter/bill/payment/customer_contact) at Con Edison, produce a single JSON
object whose keys are dbt model names and values are 1–2-sentence
business-friendly descriptions.

Model names to describe:
  dim_customer_360, dim_meter, fct_bill, fct_payment,
  stg_ccb__person, stg_ccb__account, stg_ccb__premise,
  stg_ccb__service_agreement, stg_ccb__meter, stg_ccb__bill,
  stg_ccb__payment, stg_ccb__customer_contact

Return only JSON."""


class DbtFactoryAgent(Agent):
    id = "dbt"
    name = "dbt Macro Factory Agent"
    stage = "dbt"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)

        files: list[tuple[tuple[str, ...], str]] = [
            (("dbt", "sources.yml"), SOURCES_YML),
            (("dbt", "macros", "hash_pii.sql"), MACRO_HASH),
        ]
        for entity, sql in STAGING.items():
            files.append((("dbt", "staging", f"stg_ccb__{entity}.sql"), sql))
        files += [
            (("dbt", "marts", "dim_customer_360.sql"), DIM_CUSTOMER_360),
            (("dbt", "marts", "dim_meter.sql"),        DIM_METER),
            (("dbt", "marts", "fct_bill.sql"),         FCT_BILL),
            (("dbt", "marts", "fct_payment.sql"),      FCT_PAYMENT),
        ]
        for parts, content in files:
            p = ctx.write_text(parts, content)
            self.emit(ctx, f"generated {'/'.join(parts)}", level="ok")
            self.artifact(ctx, parts[-1], p, preview=content)

        # LLM-authored descriptions
        self.emit(ctx, "Requesting business-friendly model descriptions from LLM…")
        req = ctx.request
        descs = llm.complete_json(
            SYSTEM_PROMPT,
            "Entities: " + ", ".join(e["name"] for e in req["entities"]) +
            f"\nDomain: Customer Care & Billing at {req.get('business_owner','Con Edison Customer Operations')}.",
            temperature=0.3,
        )
        if not descs:
            descs = _fallback_descriptions()
        schema_yml = _render_schema_yml(descs)
        p = ctx.write_text(("dbt", "schema.yml"), schema_yml)
        self.artifact(ctx, "schema.yml", p, preview=schema_yml)

        models = [f"stg_ccb__{e}" for e in STAGING] + [
            "dim_customer_360", "dim_meter", "fct_bill", "fct_payment",
        ]
        ctx.outputs["dbt"] = {
            "models": models,
            "descriptions": descs,
        }
        self.done(ctx, f"dbt project ready · {len(files)} files generated")
        return ctx.outputs["dbt"]


def _fallback_descriptions() -> dict:
    return {
        "dim_customer_360":     "Conformed customer view joining person, account and mailing premise. PII hashed at staging.",
        "dim_meter":            "Active/retired meters with owning service agreement and account. One row per meter.",
        "fct_bill":             "Bill fact at bill_id grain: amount, cycle, state (complete/past-due/in-review/cancelled).",
        "fct_payment":          "Payment fact at pay_id grain, joined to bill for amount-variance and overpayment flag.",
    }


def _render_schema_yml(descs: dict) -> str:
    def desc(name: str) -> str:
        return descs.get(name, "").replace('"', "'")
    return f"""# dbt/models/ccb/schema.yml (LLM-authored descriptions)
version: 2
models:
  - name: dim_customer_360
    description: "{desc('dim_customer_360')}"
    columns:
      - name: acct_id
        tests: [unique, not_null]
      - name: email_hash
        meta: {{pii: true, classification: restricted}}
        tests: [not_null]

  - name: dim_meter
    description: "{desc('dim_meter')}"
    columns:
      - name: mtr_id
        tests: [unique, not_null]

  - name: fct_bill
    description: "{desc('fct_bill')}"
    tests:
      - dbt_utils.expression_is_true:
          expression: "total_amount >= 0"
    columns:
      - name: bill_id
        tests: [unique, not_null]
      - name: acct_id
        tests:
          - relationships:
              to: ref('dim_customer_360')
              field: acct_id

  - name: fct_payment
    description: "{desc('fct_payment')}"
    columns:
      - name: pay_id
        tests: [unique, not_null]
      - name: bill_id
        tests:
          - relationships:
              to: ref('fct_bill')
              field: bill_id
"""
