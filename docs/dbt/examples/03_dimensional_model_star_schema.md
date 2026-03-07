---
title: Dimensional Modeling: Facts, Dimensions, and SCD Type 2
---

# Dimensional Modeling: Facts, Dimensions, and SCD Type 2

## Overview

This walkthrough demonstrates how to build a production-grade star schema for an e-commerce analytics platform using dbt. We'll construct:

1. **Dimension tables** that describe the "who, what, where" of your business (customers, products, stores)
2. **Fact tables** that capture measurable business events (orders, transactions)
3. **Slowly Changing Dimensions (Type 2)** that preserve historical attribute changes using dbt snapshots

Why this matters: Business stakeholders ask questions like "What was the customer's loyalty tier when they placed this order?" or "How did our product pricing changes affect sales?" Type 2 SCDs let you answer these questions accurately by maintaining a complete history of dimensional changes, rather than only seeing current state.

The star schema pattern also optimizes for the access patterns of analytical queries—wide, denormalized dimension tables joined to narrow, deep fact tables—resulting in simpler queries and better performance on columnar databases.

## Prerequisites

**Technical Requirements:**
- dbt Core 1.6+ or dbt Cloud
- A supported data warehouse (this example uses Snowflake; syntax adapts easily to BigQuery, Redshift, or Databricks)
- Python 3.8+ (for dbt installation)
- Git for version control

**Knowledge Assumed:**
- Familiarity with SQL and basic dbt concepts (models, refs, sources)
- Understanding of primary keys, foreign keys, and JOIN operations
- Basic understanding of data warehousing concepts

**Initial Setup:**

```bash
# Create a new dbt project if you don't have one
dbt init ecommerce_analytics
cd ecommerce_analytics

# Install dbt-utils for surrogate key generation
# Add to packages.yml:
```

```yaml
# packages.yml
packages:
  - package: dbt-labs/dbt_utils
    version: "1.1.1"
```

```bash
dbt deps
```

## Implementation

### Step 1: Define Source Data

Before building dimensional models, we need to declare our source systems. This creates a contract between raw data and our transformations, enabling lineage tracking and freshness monitoring.

```yaml
# models/staging/ecommerce/_ecommerce__sources.yml
version: 2

sources:
  - name: ecommerce_raw
    description: "Operational database replica from the e-commerce platform"
    database: raw_database
    schema: ecommerce
    
    # Freshness checks alert us when source data stops flowing
    freshness:
      warn_after: {count: 12, period: hour}
      error_after: {count: 24, period: hour}
    loaded_at_field: _etl_loaded_at
    
    tables:
      - name: customers
        description: "Customer account information from the user service"
        columns:
          - name: customer_id
            description: "Primary key from source system"
            tests:
              - unique
              - not_null
              
      - name: products
        description: "Product catalog from inventory management system"
        columns:
          - name: product_id
            tests:
              - unique
              - not_null
              
      - name: orders
        description: "Order headers from the order management system"
        
      - name: order_items
        description: "Line items for each order"
        
      - name: stores
        description: "Physical and online store locations"
```

### Step 2: Build Staging Models

Staging models are the first transformation layer. They perform light cleaning and renaming but preserve the grain of source data. This isolation means source system changes only require updates in one place.

```sql
-- models/staging/ecommerce/stg_ecommerce__customers.sql

with source as (
    select * from {{ source('ecommerce_raw', 'customers') }}
),

renamed as (
    select
        -- Preserve source system identifier for lineage
        customer_id as customer_id,
        
        -- Standardize naming conventions to snake_case
        first_name,
        last_name,
        
        -- Combine for convenience while keeping components available
        first_name || ' ' || last_name as full_name,
        
        email_address as email,
        phone_number as phone,
        
        -- Normalize address components
        billing_address_line_1 as billing_street_address,
        billing_city,
        billing_state,
        billing_postal_code,
        billing_country,
        
        -- Business attributes that may change over time (SCD candidates)
        loyalty_tier,                    -- bronze/silver/gold/platinum
        customer_segment,                -- retail/wholesale/enterprise
        preferred_contact_method,
        marketing_opt_in_flag as is_marketing_opted_in,
        
        -- Timestamps for tracking
        account_created_at,
        last_login_at,
        
        -- ETL metadata
        _etl_loaded_at as loaded_at
        
    from source
    
    -- Filter out test accounts that pollute analytics
    where email_address not like '%@test.internal.com'
      and customer_id > 0  -- Exclude system/placeholder records
)

select * from renamed
```

```sql
-- models/staging/ecommerce/stg_ecommerce__products.sql

with source as (
    select * from {{ source('ecommerce_raw', 'products') }}
),

renamed as (
    select
        product_id,
        sku,
        product_name,
        product_description,
        
        -- Category hierarchy for drill-down analysis
        category_level_1 as category,           -- e.g., "Electronics"
        category_level_2 as subcategory,        -- e.g., "Computers"
        category_level_3 as product_type,       -- e.g., "Laptops"
        
        brand_name as brand,
        supplier_id,
        
        -- Pricing (changes tracked via SCD)
        unit_cost,
        list_price,
        
        -- Inventory classification
        weight_kg,
        is_fragile,
        is_hazardous,
        
        -- Lifecycle flags
        case 
            when discontinued_at is not null then true
            else false
        end as is_discontinued,
        
        product_launch_date as launched_at,
        discontinued_at,
        _etl_loaded_at as loaded_at
        
    from source
    
    where product_id is not null
)

select * from renamed
```

```sql
-- models/staging/ecommerce/stg_ecommerce__orders.sql

with source as (
    select * from {{ source('ecommerce_raw', 'orders') }}
),

renamed as (
    select
        order_id,
        customer_id,
        store_id,
        
        -- Order timestamps (critical for fact table partitioning)
        order_placed_at,
        order_confirmed_at,
        order_shipped_at,
        order_delivered_at,
        order_cancelled_at,
        
        -- Derived status for easier filtering
        case
            when order_cancelled_at is not null then 'cancelled'
            when order_delivered_at is not null then 'delivered'
            when order_shipped_at is not null then 'shipped'
            when order_confirmed_at is not null then 'confirmed'
            else 'pending'
        end as order_status,
        
        -- Financial amounts (stored in cents in source, convert to dollars)
        order_subtotal_cents / 100.0 as order_subtotal,
        discount_amount_cents / 100.0 as discount_amount,
        shipping_amount_cents / 100.0 as shipping_amount,
        tax_amount_cents / 100.0 as tax_amount,
        (order_subtotal_cents + shipping_amount_cents + tax_amount_cents - discount_amount_cents) / 100.0 as order_total,
        
        -- Order metadata
        order_source_channel,    -- web/mobile_app/phone/in_store
        promo_code_applied,
        shipping_method,
        payment_method,
        
        _etl_loaded_at as loaded_at
        
    from source
)

select * from renamed
```

```sql
-- models/staging/ecommerce/stg_ecommerce__order_items.sql

with source as (
    select * from {{ source('ecommerce_raw', 'order_items') }}
),

renamed as (
    select
        order_item_id,
        order_id,
        product_id,
        
        quantity,
        
        -- Pricing at time of purchase (source of truth for revenue)
        unit_price_cents / 100.0 as unit_price,
        line_discount_cents / 100.0 as line_discount,
        
        -- Calculate line total
        (quantity * unit_price_cents - line_discount_cents) / 100.0 as line_total,
        
        -- Fulfillment tracking at line level
        fulfillment_status,
        shipped_from_warehouse_id,
        
        _etl_loaded_at as loaded_at
        
    from source
)

select * from renamed
```

### Step 3: Implement SCD Type 2 with dbt Snapshots

Snapshots are dbt's mechanism for tracking historical changes. When a tracked column changes, dbt closes out the old record (sets `dbt_valid_to`) and creates a new one. This preserves the complete history of dimensional changes.

```sql
-- snapshots/snap_customers.sql

{% snapshot snap_customers %}

{{
    config(
        -- Target location for snapshot table
        target_database='analytics',
        target_schema='snapshots',
        
        -- Unique identifier from source - must be truly unique and stable
        unique_key='customer_id',
        
        -- Track changes using a timestamp column from source
        -- Alternative: strategy='check' with check_cols for systems without reliable timestamps
        strategy='timestamp',
        updated_at='loaded_at',
        
        -- Columns to invalidate old record when changed
        -- Be selective: only track columns where history matters for analysis
        invalidate_hard_deletes=true
    )
}}

select
    customer_id,
    full_name,
    email,
    
    -- These are the SCD-tracked attributes
    -- Changes here create new historical records
    loyalty_tier,
    customer_segment,
    
    -- Include geographic attributes for regional analysis over time
    billing_city,
    billing_state,
    billing_country,
    
    is_marketing_opted_in,
    account_created_at,
    loaded_at

from {{ ref('stg_ecommerce__customers') }}

{% endsnapshot %}
```

```sql
-- snapshots/snap_products.sql

{% snapshot snap_products %}

{{
    config(
        target_database='analytics',
        target_schema='snapshots',
        unique_key='product_id',
        strategy='timestamp',
        updated_at='loaded_at',
        invalidate_hard_deletes=true
    )
}}

select
    product_id,
    sku,
    product_name,
    
    -- Category changes are business-critical to track
    -- Helps answer: "Did reclassifying this product affect sales?"
    category,
    subcategory,
    product_type,
    
    brand,
    
    -- Price tracking is essential for margin analysis
    unit_cost,
    list_price,
    
    is_discontinued,
    launched_at,
    loaded_at

from {{ ref('stg_ecommerce__products') }}

{% endsnapshot %}
```

### Step 4: Build Dimension Tables with Surrogate Keys

Dimension tables wrap snapshots with surrogate keys and user-friendly validity columns. Surrogate keys insulate your fact tables from source system key changes and enable efficient joins.

```sql
-- models/marts/core/dim_customers.sql

{{
    config(
        materialized='table',
        
        -- Cluster on commonly filtered columns for query performance
        cluster_by=['customer_segment', 'billing_country']
    )
}}

with snapshot_data as (
    select * from {{ ref('snap_customers') }}
),

-- Add surrogate key and clean up snapshot metadata columns
dimensionalized as (
    select
        -- Generate a stable surrogate key from business key + validity window
        -- This key is what fact tables will reference
        {{ dbt_utils.generate_surrogate_key(['customer_id', 'dbt_valid_from']) }} as customer_sk,
        
        -- Natural key preserved for debugging and late-arriving facts
        customer_id as customer_nk,
        
        full_name,
        email,
        
        -- SCD-tracked attributes
        loyalty_tier,
        customer_segment,
        billing_city,
        billing_state,
        billing_country,
        is_marketing_opted_in,
        
        -- Customer lifecycle metrics
        account_created_at,
        
        -- SCD Type 2 validity columns with user-friendly naming
        dbt_valid_from as valid_from,
        
        -- Replace NULL (current record) with far-future date for easier filtering
        coalesce(dbt_valid_to, '9999-12-31'::timestamp) as valid_to,
        
        -- Boolean flag for current record - simplifies most queries
        case 
            when dbt_valid_to is null then true 
            else false 
        end as is_current,
        
        -- Row metadata for debugging
        dbt_scd_id as scd_hash,
        dbt_updated_at as snapshot_updated_at
        
    from snapshot_data
)

select * from dimensionalized
```

```sql
-- models/marts/core/dim_products.sql

{{
    config(
        materialized='table',
        cluster_by=['category', 'brand']
    )
}}

with snapshot_data as (
    select * from {{ ref('snap_products') }}
),

dimensionalized as (
    select
        {{ dbt_utils.generate_surrogate_key(['product_id', 'dbt_valid_from']) }} as product_sk,
        product_id as product_nk,
        
        sku,
        product_name,
        