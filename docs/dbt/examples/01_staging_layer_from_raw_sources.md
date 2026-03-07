---
title: Building a Staging Layer with Source Freshness Checks
---

# Building a Staging Layer with Source Freshness Checks

## Overview

The staging layer is the first transformation step after raw data lands in your warehouse. Its job is deceptively simple: rename columns to consistent conventions, cast types explicitly, filter out junk rows, and provide a clean interface for downstream models. But getting this foundation wrong cascades problems throughout your entire project.

This walkthrough demonstrates how to build a production-quality staging layer for an e-commerce platform's order data. We'll declare sources with freshness expectations, implement staging models that follow dbt best practices, and wire up generic tests that catch data quality issues before they propagate. By the end, you'll have a pattern you can replicate for every new data source your team onboards.

## Prerequisites

**Environment:**
- dbt Core 1.7+ or dbt Cloud
- A configured warehouse connection (this example uses Snowflake syntax, but adapts easily)
- Raw data loaded into a `raw` schema (via Fivetran, Airbyte, or custom ELT)

**Knowledge:**
- Basic dbt concepts: models, refs, sources
- YAML configuration files
- SQL fundamentals

**Project Structure:**
Your dbt project should already have the standard directory layout:
```
your_project/
├── dbt_project.yml
├── models/
│   ├── staging/
│   └── marts/
└── macros/
```

## Implementation

### Step 1: Declare Your Sources

Before writing any SQL, we declare the raw tables as sources. This serves three purposes: it creates a dependency graph that dbt can track, it enables freshness monitoring, and it provides a single place to document the raw data contract.

Create `models/staging/ecommerce/_ecommerce__sources.yml`:

```yaml
version: 2

sources:
  - name: ecommerce_raw
    description: >
      Raw e-commerce data replicated from the production PostgreSQL database
      via Fivetran. Syncs every 6 hours.
    database: analytics  # Explicit database reference; remove if using default
    schema: raw_ecommerce
    
    # Freshness config applies to all tables unless overridden
    freshness:
      warn_after: {count: 12, period: hour}
      error_after: {count: 24, period: hour}
    loaded_at_field: _fivetran_synced  # Column that indicates when row was loaded
    
    tables:
      - name: orders
        description: One row per order placed on the website
        columns:
          - name: id
            description: Primary key from source system
          - name: _fivetran_synced
            description: Timestamp when Fivetran loaded this row
        
        # Override freshness for this critical table
        freshness:
          warn_after: {count: 6, period: hour}
          error_after: {count: 12, period: hour}

      - name: order_items
        description: One row per line item within an order
        columns:
          - name: id
            description: Primary key from source system
          - name: order_id
            description: Foreign key to orders.id

      - name: customers
        description: One row per registered customer
        freshness:
          # Customer data updates less frequently; relax the check
          warn_after: {count: 24, period: hour}
          error_after: {count: 48, period: hour}

      - name: products
        description: Product catalog, synced from inventory system
        # Products table doesn't have _fivetran_synced; use different column
        loaded_at_field: updated_at
```

**Why separate the sources file?** Keeping source declarations in their own file (prefixed with underscore) makes them easy to find and maintains separation from model configurations. The underscore prefix is a dbt convention indicating "configuration, not a model."

### Step 2: Create the Staging Directory Structure

Organize staging models by source system. This scales well as you add more data sources:

```
models/
└── staging/
    └── ecommerce/
        ├── _ecommerce__sources.yml
        ├── _ecommerce__models.yml
        ├── stg_ecommerce__orders.sql
        ├── stg_ecommerce__order_items.sql
        ├── stg_ecommerce__customers.sql
        └── stg_ecommerce__products.sql
```

The naming convention `stg_<source>__<entity>` is deliberate. The double underscore separates source from entity, making parsing unambiguous. When you have 50 staging models, you'll appreciate the consistency.

### Step 3: Implement the Orders Staging Model

Create `models/staging/ecommerce/stg_ecommerce__orders.sql`:

```sql
with source as (

    select * from {{ source('ecommerce_raw', 'orders') }}

),

renamed as (

    select
        -- Primary key
        id as order_id,
        
        -- Foreign keys
        customer_id,
        
        -- Timestamps: always cast explicitly even if type looks correct
        -- Source system uses timestamp without timezone; we standardize to UTC
        convert_timezone('America/New_York', 'UTC', created_at) as ordered_at,
        convert_timezone('America/New_York', 'UTC', updated_at) as order_updated_at,
        convert_timezone('America/New_York', 'UTC', shipped_at) as shipped_at,
        convert_timezone('America/New_York', 'UTC', delivered_at) as delivered_at,
        
        -- Status fields: lowercase for consistent downstream filtering
        lower(status) as order_status,
        lower(shipping_method) as shipping_method,
        
        -- Financial fields: cast to numeric with explicit precision
        -- Source stores cents as integers; convert to dollars
        cast(subtotal_cents as numeric(12, 2)) / 100 as subtotal,
        cast(shipping_cents as numeric(12, 2)) / 100 as shipping_amount,
        cast(tax_cents as numeric(12, 2)) / 100 as tax_amount,
        cast(total_cents as numeric(12, 2)) / 100 as order_total,
        
        -- Discount code can be null; preserve that semantic
        nullif(trim(discount_code), '') as discount_code,
        
        -- Boolean: source uses 0/1 integers
        case when is_gift = 1 then true else false end as is_gift_order,
        
        -- Metadata for debugging lineage
        _fivetran_synced as _loaded_at

    from source
    
    -- Filter out test orders that leak into production
    -- Business rule: test orders always have customer_id < 1000
    where customer_id >= 1000

)

select * from renamed
```

**Key decisions explained:**

1. **CTE naming**: `source` → `renamed` is the minimal staging pattern. More complex staging might add `filtered`, `deduplicated`, etc.

2. **Explicit casting**: Even when types look correct, explicit casts document your expectations and catch upstream schema changes early.

3. **Timezone handling**: Raw data often lacks timezone awareness. Converting to UTC in staging means downstream models never have to think about it.

4. **Cents to dollars**: Financial transformations like this belong in staging. Every downstream model should work with dollars, never cents.

5. **Filtering test data**: Put "universal filters" here rather than repeating them in every downstream model.

### Step 4: Implement the Order Items Staging Model

Create `models/staging/ecommerce/stg_ecommerce__order_items.sql`:

```sql
with source as (

    select * from {{ source('ecommerce_raw', 'order_items') }}

),

renamed as (

    select
        -- Generate a surrogate key when source PK isn't trustworthy
        -- or when you need a deterministic key for incremental models
        {{ dbt_utils.generate_surrogate_key(['id', 'order_id']) }} as order_item_key,
        
        id as order_item_id,
        order_id,
        product_id,
        
        quantity,
        
        -- Unit price at time of purchase (not current catalog price)
        cast(unit_price_cents as numeric(12, 2)) / 100 as unit_price,
        cast(unit_price_cents * quantity as numeric(12, 2)) / 100 as line_total,
        
        -- Handle potential nulls in optional fields
        coalesce(gift_message, '') as gift_message,
        
        _fivetran_synced as _loaded_at

    from source
    
    -- Defensive filter: negative quantities indicate returns, handled elsewhere
    where quantity > 0

)

select * from renamed
```

### Step 5: Implement the Customers Staging Model

Create `models/staging/ecommerce/stg_ecommerce__customers.sql`:

```sql
with source as (

    select * from {{ source('ecommerce_raw', 'customers') }}

),

-- Deduplicate: source system occasionally produces duplicate customer records
-- Keep the most recently updated version
deduplicated as (

    select
        *,
        row_number() over (
            partition by id 
            order by updated_at desc
        ) as _row_num
    
    from source

),

renamed as (

    select
        id as customer_id,
        
        -- PII handling: hash email for non-production environments
        {% if target.name == 'prod' %}
            email
        {% else %}
            md5(lower(trim(email))) as email_hash
        {% endif %} as customer_email,
        
        -- Name standardization
        initcap(trim(first_name)) as first_name,
        initcap(trim(last_name)) as last_name,
        
        -- Derive a display name for reporting
        initcap(trim(first_name)) || ' ' || left(trim(last_name), 1) || '.' as display_name,
        
        -- Geographic fields
        lower(trim(country_code)) as country_code,
        upper(trim(state_province)) as state_province,
        trim(city) as city,
        trim(postal_code) as postal_code,
        
        -- Account status
        lower(status) as customer_status,
        case 
            when lower(status) = 'active' then true 
            else false 
        end as is_active_customer,
        
        -- Timestamps
        convert_timezone('America/New_York', 'UTC', created_at) as customer_created_at,
        convert_timezone('America/New_York', 'UTC', updated_at) as customer_updated_at,
        
        _fivetran_synced as _loaded_at

    from deduplicated
    where _row_num = 1

)

select * from renamed
```

**Why deduplication in staging?** When your source system has known data quality issues, fixing them in staging prevents every downstream model from having to handle it. Document the deduplication logic clearly.

### Step 6: Implement the Products Staging Model

Create `models/staging/ecommerce/stg_ecommerce__products.sql`:

```sql
with source as (

    select * from {{ source('ecommerce_raw', 'products') }}

),

renamed as (

    select
        id as product_id,
        sku,
        
        -- Product names often have trailing whitespace from source
        trim(name) as product_name,
        
        -- Category hierarchy comes as pipe-delimited string
        -- Parse into separate fields for easier filtering
        split_part(category_path, '|', 1) as category_level_1,
        split_part(category_path, '|', 2) as category_level_2,
        split_part(category_path, '|', 3) as category_level_3,
        category_path as category_full_path,
        
        -- Current pricing
        cast(price_cents as numeric(10, 2)) / 100 as current_price,
        cast(cost_cents as numeric(10, 2)) / 100 as unit_cost,
        
        -- Inventory flags
        case 
            when inventory_count > 0 then true 
            else false 
        end as is_in_stock,
        inventory_count,
        
        -- Product lifecycle
        lower(status) as product_status,
        case 
            when lower(status) = 'discontinued' then true 
            else false 
        end as is_discontinued,
        
        updated_at as _loaded_at

    from source
    
    -- Exclude draft products that aren't yet published
    where lower(status) != 'draft'

)

select * from renamed
```

### Step 7: Configure Models and Add Tests

Create `models/staging/ecommerce/_ecommerce__models.yml`:

```yaml
version: 2

models:
  - name: stg_ecommerce__orders
    description: >
      Cleaned orders data with standardized column names, UTC timestamps,
      and dollar amounts. Test orders (customer_id < 1000) are excluded.
    columns:
      - name: order_id
        description: Primary key
        tests:
          - unique
          - not_null
      
      - name: customer_id
        description: Foreign key to stg_ecommerce__customers
        tests:
          - not_null
          - relationships:
              to: ref('stg_ecommerce__customers')
              field: customer_id
              # Warn rather than fail: customer data syncs on different schedule
              severity: warn
      
      - name: order_status
        description: Current order status (pending, confirmed, shipped, delivered, cancelled)
        tests:
          - not_null
          - accepted_values:
              values: ['pending', 'confirmed', 'shipped', 'delivered', 'cancelled']
              # Allow new statuses but surface them for investigation
              config:
                severity: warn
      
      - name: order_total
        description: Total order amount in USD
        tests:
          - not_null
          - dbt_utils.accepted_range:
              min_value: 0
              max_value: 50000  # Flag anomalies over $50k for review

  - name: stg_ecommerce__order_items
    description: >
      Line items for each order. Returns (negative quantities)