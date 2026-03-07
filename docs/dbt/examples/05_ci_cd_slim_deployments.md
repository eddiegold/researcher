---
title: CI/CD Pipeline with State-Based Slim CI and Contract Testing
---

# CI/CD Pipeline with State-Based Slim CI and Contract Testing

## Overview

In mature dbt deployments, running your entire project on every pull request becomes untenable. A project with 500+ models might take 45 minutes to build—far too long for rapid iteration. State-based "Slim CI" solves this by comparing your feature branch against production state and running only what changed.

This walkthrough implements a complete CI/CD pipeline that:

1. **Runs only modified models and their downstream dependents** using dbt's state comparison
2. **Enforces model contracts** to catch breaking schema changes before they hit production
3. **Uses deferred references** so CI builds can reference production tables for unchanged upstream models
4. **Deploys to environment-specific schemas** with proper state artifact management

We'll build this for a realistic e-commerce analytics platform with order processing, customer segmentation, and revenue reporting models.

## Prerequisites

**Required installations:**
- dbt-core >= 1.6.0 (for contract enforcement features)
- A cloud data warehouse (examples use Snowflake, but concepts apply to BigQuery/Redshift/Databricks)
- GitHub Actions (or adapt to your CI platform)
- A cloud storage location for production artifacts (S3/GCS/Azure Blob)

**Required knowledge:**
- Basic dbt project structure and model configuration
- YAML schema file syntax
- Git branching workflows
- Basic CI/CD concepts

**Project structure we'll build:**
```
ecommerce_analytics/
├── dbt_project.yml
├── profiles.yml
├── models/
│   ├── staging/
│   │   ├── _staging__models.yml
│   │   ├── stg_orders.sql
│   │   └── stg_customers.sql
│   ├── intermediate/
│   │   ├── _intermediate__models.yml
│   │   └── int_orders_enriched.sql
│   └── marts/
│       ├── _marts__models.yml
│       ├── fct_orders.sql
│       └── dim_customers.sql
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
└── scripts/
    └── fetch_prod_artifacts.sh
```

## Implementation

### Step 1: Configure Environment-Specific Targets

First, establish a profiles configuration that supports distinct development, CI, and production environments. The key insight here is that CI needs its own isolated schema to avoid polluting production while still being able to reference production tables for unchanged models.

**profiles.yml** (stored securely, not in repo):
```yaml
ecommerce_analytics:
  target: dev  # Default for local development
  outputs:
    # Local development - each developer gets their own schema
    dev:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_USER') }}"
      password: "{{ env_var('SNOWFLAKE_PASSWORD') }}"
      role: transformer_dev
      database: analytics_dev
      warehouse: transforming_xs
      schema: "dbt_{{ env_var('USER', 'local') }}"  # Creates dbt_jsmith, dbt_mlee, etc.
      threads: 4

    # CI environment - ephemeral schema per PR
    ci:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_CI_USER') }}"
      password: "{{ env_var('SNOWFLAKE_CI_PASSWORD') }}"
      role: transformer_ci
      database: analytics_ci
      warehouse: transforming_sm
      # Schema includes PR number for isolation and easy cleanup
      schema: "pr_{{ env_var('PR_NUMBER', 'unknown') }}"
      threads: 8

    # Production - restricted access, larger warehouse
    prod:
      type: snowflake
      account: "{{ env_var('SNOWFLAKE_ACCOUNT') }}"
      user: "{{ env_var('SNOWFLAKE_PROD_USER') }}"
      password: "{{ env_var('SNOWFLAKE_PROD_PASSWORD') }}"
      role: transformer_prod
      database: analytics
      warehouse: transforming_lg
      schema: core
      threads: 16
```

### Step 2: Define Model Contracts for Critical Models

Contracts are the foundation of breaking-change detection. When a contract exists, dbt will fail the build if the actual columns don't match the declared schema. This is essential for models consumed by downstream systems (BI tools, reverse ETL, ML pipelines).

**models/marts/_marts__models.yml:**
```yaml
version: 2

models:
  - name: fct_orders
    description: "Order fact table - core revenue reporting grain"
    # Contract enforcement: dbt will validate that built table matches this schema
    config:
      contract:
        enforced: true
    
    # Access modifier controls whether other projects can ref() this model
    access: public
    
    # Latest version - enables gradual schema migrations
    latest_version: 1
    
    columns:
      - name: order_id
        description: "Primary key - unique order identifier"
        data_type: varchar(32)  # Explicit types required for contracts
        constraints:
          - type: primary_key
          - type: not_null
        data_tests:
          - unique
          - not_null

      - name: customer_id
        description: "Foreign key to dim_customers"
        data_type: varchar(32)
        constraints:
          - type: not_null
        data_tests:
          - not_null
          - relationships:
              to: ref('dim_customers')
              field: customer_id

      - name: order_date
        description: "Date the order was placed (UTC)"
        data_type: date
        constraints:
          - type: not_null

      - name: shipped_date
        description: "Date the order shipped, null if not yet shipped"
        data_type: date
        # No not_null constraint - nullable by design

      - name: order_status
        description: "Current order status"
        data_type: varchar(20)
        constraints:
          - type: not_null
        data_tests:
          - accepted_values:
              values: ['pending', 'processing', 'shipped', 'delivered', 'cancelled', 'refunded']

      - name: item_count
        description: "Total number of items in the order"
        data_type: integer
        constraints:
          - type: not_null

      - name: subtotal_cents
        description: "Order subtotal before tax/shipping in cents"
        data_type: integer
        constraints:
          - type: not_null

      - name: tax_cents
        description: "Tax amount in cents"
        data_type: integer
        constraints:
          - type: not_null

      - name: shipping_cents
        description: "Shipping cost in cents"
        data_type: integer
        constraints:
          - type: not_null

      - name: total_cents
        description: "Final order total in cents (subtotal + tax + shipping)"
        data_type: integer
        constraints:
          - type: not_null

      - name: currency_code
        description: "ISO 4217 currency code"
        data_type: varchar(3)
        constraints:
          - type: not_null

      - name: _loaded_at
        description: "Timestamp when this record was loaded by dbt"
        data_type: timestamp_ntz
        constraints:
          - type: not_null

  - name: dim_customers
    description: "Customer dimension - slowly changing dimension type 2"
    config:
      contract:
        enforced: true
    access: public
    latest_version: 1
    
    columns:
      - name: customer_id
        description: "Primary key - surrogate key for customer dimension"
        data_type: varchar(32)
        constraints:
          - type: primary_key
          - type: not_null

      - name: customer_natural_key
        description: "Natural key from source system"
        data_type: varchar(64)
        constraints:
          - type: not_null

      - name: email_domain
        description: "Domain extracted from customer email (PII-safe)"
        data_type: varchar(255)
        # Nullable - some customers don't have email

      - name: customer_segment
        description: "Computed customer value segment"
        data_type: varchar(20)
        constraints:
          - type: not_null
        data_tests:
          - accepted_values:
              values: ['new', 'returning', 'loyal', 'at_risk', 'churned']

      - name: first_order_date
        description: "Date of customer's first order"
        data_type: date
        # Nullable - customer may exist without orders

      - name: lifetime_order_count
        description: "Total orders placed by customer"
        data_type: integer
        constraints:
          - type: not_null

      - name: lifetime_revenue_cents
        description: "Total revenue from customer in cents"
        data_type: integer
        constraints:
          - type: not_null

      - name: is_current
        description: "SCD2 current record flag"
        data_type: boolean
        constraints:
          - type: not_null

      - name: valid_from
        description: "SCD2 record validity start"
        data_type: timestamp_ntz
        constraints:
          - type: not_null

      - name: valid_to
        description: "SCD2 record validity end, null for current"
        data_type: timestamp_ntz
```

### Step 3: Implement Models with Contract-Compliant Typing

When contracts are enforced, you must explicitly cast columns to match declared types. This is intentional friction—it forces you to think about data types at modeling time rather than discovering mismatches in production.

**models/marts/fct_orders.sql:**
```sql
{{
    config(
        materialized='incremental',
        unique_key='order_id',
        on_schema_change='append_new_columns',  -- Safe default for incremental
        incremental_strategy='merge'
    )
}}

with orders_enriched as (
    select * from {{ ref('int_orders_enriched') }}
    {% if is_incremental() %}
    -- Only process orders updated since last run
    where updated_at > (select max(_loaded_at) from {{ this }})
    {% endif %}
),

final as (
    select
        -- Primary key: explicit cast to match contract
        order_id::varchar(32) as order_id,
        
        -- Foreign key to customer dimension
        customer_id::varchar(32) as customer_id,
        
        -- Date dimensions
        order_date::date as order_date,
        shipped_date::date as shipped_date,  -- May be null
        
        -- Order attributes
        order_status::varchar(20) as order_status,
        item_count::integer as item_count,
        
        -- Money fields stored as cents to avoid floating point issues
        subtotal_cents::integer as subtotal_cents,
        tax_cents::integer as tax_cents,
        shipping_cents::integer as shipping_cents,
        (subtotal_cents + tax_cents + shipping_cents)::integer as total_cents,
        
        currency_code::varchar(3) as currency_code,
        
        -- Audit column
        current_timestamp()::timestamp_ntz as _loaded_at
    
    from orders_enriched
)

select * from final
```

**models/intermediate/int_orders_enriched.sql:**
```sql
{{
    config(
        materialized='view'  -- Intermediate models as views reduce storage costs
    )
}}

with orders as (
    select * from {{ ref('stg_orders') }}
),

customers as (
    select * from {{ ref('stg_customers') }}
),

-- Calculate order-level metrics
order_items_agg as (
    select
        order_id,
        count(*) as item_count,
        sum(unit_price_cents * quantity) as subtotal_cents
    from {{ ref('stg_order_items') }}
    group by 1
),

final as (
    select
        o.order_id,
        o.customer_id,
        o.order_date,
        o.shipped_date,
        o.order_status,
        o.currency_code,
        o.updated_at,
        
        coalesce(oi.item_count, 0) as item_count,
        coalesce(oi.subtotal_cents, 0) as subtotal_cents,
        
        -- Tax calculation: 8.25% sales tax (simplified)
        round(coalesce(oi.subtotal_cents, 0) * 0.0825)::integer as tax_cents,
        
        -- Shipping: free over $50, otherwise $5.99
        case 
            when coalesce(oi.subtotal_cents, 0) >= 5000 then 0
            else 599
        end as shipping_cents
    
    from orders o
    left join order_items_agg oi on o.order_id = oi.order_id
)

select * from final
```

### Step 4: Create Production Artifact Fetch Script

Slim CI requires comparing your branch against production's last successful state. This script fetches the production `manifest.json` from cloud storage. The manifest contains the complete compiled state of your project.

**scripts/fetch_prod_artifacts.sh:**
```bash
#!/bin/bash
set -euo pipefail

# This script fetches production dbt artifacts for state comparison.
# Called during CI to enable --state flag and deferred references.

ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-s3://ecommerce-analytics-dbt-artifacts}"
PROD_RUN_RESULTS_PATH="${ARTIFACT_BUCKET}/prod/latest"
LOCAL_STATE_DIR="./prod_artifacts"

echo "Fetching production artifacts from ${PROD_RUN_RESULTS_PATH}..."

# Create local directory for production state
mkdir -p "${LOCAL_STATE_DIR}"

#