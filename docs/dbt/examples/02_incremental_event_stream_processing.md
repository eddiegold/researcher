---
title: Incremental Models for High-Volume Event Data
---

# Incremental Models for High-Volume Event Data

## Overview

When your analytics warehouse ingests billions of clickstream events daily, running full table scans on every dbt run becomes untenable. A single run might take hours, cost thousands of dollars in compute, and block downstream consumers. Incremental models solve this by processing only new or changed data since the last run.

This walkthrough demonstrates building a production-grade incremental model that:

1. Processes only new clickstream events using timestamp-based filtering
2. Handles late-arriving data through configurable lookback windows
3. Uses merge strategies to correctly handle duplicates and updates
4. Avoids common pitfalls that silently reintroduce full table scans

We'll build a model that transforms raw clickstream events into a sessionized fact table, processing approximately 500M events per day while keeping run times under 15 minutes.

## Prerequisites

**Infrastructure:**
- dbt Core 1.6+ or dbt Cloud
- Snowflake, BigQuery, or Databricks (examples use Snowflake syntax with adapter notes)
- Access to a warehouse with at least MEDIUM sizing for initial backfill

**Knowledge:**
- Familiarity with dbt models, sources, and the ref() function
- Understanding of SQL merge operations
- Basic awareness of your data warehouse's partitioning/clustering capabilities

**Source Data:**
Your raw clickstream events should exist in a source table with this approximate shape:

```sql
-- Example: raw.clickstream_events
-- ~500M rows/day, ~2 years of history
CREATE TABLE raw.clickstream_events (
    event_id STRING,           -- UUID, globally unique
    user_id STRING,            -- Can be null for anonymous users
    anonymous_id STRING,       -- Device/cookie identifier
    event_type STRING,         -- 'page_view', 'click', 'form_submit', etc.
    event_timestamp TIMESTAMP_NTZ,
    page_url STRING,
    referrer_url STRING,
    device_type STRING,
    browser STRING,
    country_code STRING,
    session_id STRING,         -- May be null, populated by upstream system
    properties VARIANT,        -- JSON blob of event-specific data
    received_at TIMESTAMP_NTZ, -- When our ingestion pipeline received it
    _loaded_at TIMESTAMP_NTZ   -- When it landed in the warehouse
)
CLUSTER BY (TO_DATE(event_timestamp), event_type);
```

**Project Structure:**
```
your_dbt_project/
├── dbt_project.yml
├── models/
│   └── marts/
│       └── clickstream/
│           ├── _clickstream__models.yml
│           ├── fct_clickstream_events.sql
│           └── int_clickstream_sessionized.sql
└── macros/
    └── incremental_lookback.sql
```

## Implementation

### Step 1: Define the Source with Freshness Checks

Before building incremental models, define your source with freshness expectations. This catches upstream pipeline failures before they cascade.

**models/staging/sources.yml:**

```yaml
version: 2

sources:
  - name: raw_clickstream
    database: raw
    schema: clickstream
    
    # Freshness tells dbt to alert if data stops flowing
    freshness:
      warn_after: {count: 2, period: hour}
      error_after: {count: 6, period: hour}
    
    # Use _loaded_at for freshness, not event_timestamp
    # Events can be old; what matters is whether they're still arriving
    loaded_at_field: _loaded_at
    
    tables:
      - name: events
        identifier: clickstream_events
        description: "Raw clickstream events from Segment/Snowplow/etc."
        
        columns:
          - name: event_id
            description: "Primary key - UUID generated at collection time"
            tests:
              - not_null
              # unique test would be too expensive on 2B+ rows
              # we handle duplicates in the incremental merge instead
```

### Step 2: Create a Lookback Window Macro

Late-arriving data is the primary gotcha with incremental models. Events might arrive hours or days after they occurred due to:
- Mobile apps syncing when connectivity resumes
- Batch uploads from offline systems  
- Pipeline replay after failure recovery

We'll create a macro that calculates a lookback window, making it configurable per model and environment.

**macros/incremental_lookback.sql:**

```sql
{#
    Calculate the timestamp threshold for incremental loads.
    
    In production, we look back 3 hours to catch late-arriving data.
    In CI/dev, we use 1 hour for faster iteration.
    
    The lookback trades off:
    - Too short: miss late-arriving events, causing data gaps
    - Too long: reprocess too much data, negating incremental benefits
    
    Monitor your source data's arrival patterns to tune this.
#}

{% macro get_incremental_lookback_timestamp(
    timestamp_column='event_timestamp',
    default_lookback_hours=3
) %}

    {#- Allow override via dbt variable for testing/backfills -#}
    {% set lookback_hours = var('incremental_lookback_hours', default_lookback_hours) %}
    
    {#- Use shorter lookback in CI to speed up tests -#}
    {% if target.name == 'ci' %}
        {% set lookback_hours = 1 %}
    {% endif %}
    
    (
        SELECT 
            DATEADD(
                hour, 
                -{{ lookback_hours }}, 
                MAX({{ timestamp_column }})
            )
        FROM {{ this }}
    )

{% endmacro %}
```

### Step 3: Build the Core Incremental Model

Now we build the main fact table. This model:
- Extracts and flattens the raw events
- Applies business logic (event categorization, URL parsing)
- Handles deduplication via merge
- Processes only the incremental window on subsequent runs

**models/marts/clickstream/fct_clickstream_events.sql:**

```sql
{{
    config(
        materialized='incremental',
        
        -- MERGE strategy handles:
        -- 1. New events (INSERT)
        -- 2. Duplicate events from late-arriving data (no-op, already exists)
        -- 3. Updated events if your source supports updates (UPDATE)
        incremental_strategy='merge',
        
        -- The unique key for merge matching
        -- Using event_id assumes your collection system guarantees uniqueness
        unique_key='event_id',
        
        -- Snowflake: cluster on date for efficient time-range queries
        -- BigQuery users: use partition_by instead
        cluster_by=['event_date', 'event_type'],
        
        -- BigQuery partition config (uncomment if using BQ):
        -- partition_by={
        --     "field": "event_date",
        --     "data_type": "date",
        --     "granularity": "day"
        -- },
        
        -- Merge behavior: what to do when unique_key matches
        -- 'merge_update_columns' limits which columns get updated
        -- This is safer than updating everything and slightly faster
        merge_update_columns=[
            'user_id',           -- User might authenticate after initial event
            'session_id',        -- Session might be assigned retroactively
            'processed_at'       -- Track when we last touched this row
        ]
    )
}}

WITH source_events AS (
    
    SELECT
        event_id,
        user_id,
        anonymous_id,
        event_type,
        event_timestamp,
        
        -- Parse out the event date for partitioning/clustering
        DATE(event_timestamp) AS event_date,
        
        -- Extract hour for time-of-day analysis
        EXTRACT(HOUR FROM event_timestamp) AS event_hour,
        
        page_url,
        
        -- Parse URL components - these are expensive so we do it once here
        PARSE_URL(page_url):host::STRING AS page_domain,
        PARSE_URL(page_url):path::STRING AS page_path,
        SPLIT_PART(PARSE_URL(page_url):path::STRING, '/', 2) AS page_section,
        
        referrer_url,
        PARSE_URL(referrer_url):host::STRING AS referrer_domain,
        
        device_type,
        browser,
        country_code,
        session_id,
        
        -- Extract commonly-accessed properties from the JSON blob
        -- This avoids repeated JSON parsing in downstream queries
        properties:product_id::STRING AS product_id,
        properties:category::STRING AS product_category,
        properties:search_query::STRING AS search_query,
        properties:button_name::STRING AS button_name,
        
        -- Categorize events into broader groups for easier filtering
        CASE
            WHEN event_type IN ('page_view', 'screen_view') THEN 'pageview'
            WHEN event_type IN ('click', 'tap', 'button_click') THEN 'interaction'
            WHEN event_type IN ('form_submit', 'sign_up', 'login') THEN 'conversion'
            WHEN event_type IN ('add_to_cart', 'remove_from_cart', 'purchase') THEN 'commerce'
            WHEN event_type LIKE 'video_%' THEN 'media'
            ELSE 'other'
        END AS event_category,
        
        -- Track data lineage
        received_at,
        _loaded_at,
        CURRENT_TIMESTAMP() AS processed_at
        
    FROM {{ source('raw_clickstream', 'events') }}
    
    WHERE 1=1
        -- Always filter out obviously invalid data
        AND event_id IS NOT NULL
        AND event_timestamp IS NOT NULL
        -- Filter out events too far in the future (likely bad data)
        AND event_timestamp <= CURRENT_TIMESTAMP()
        -- Filter out events before our business existed (definitely bad data)
        AND event_timestamp >= '2020-01-01'
        
        {% if is_incremental() %}
            -- CRITICAL: This is where incremental magic happens
            -- We only process events newer than our lookback threshold
            --
            -- Why event_timestamp and not _loaded_at?
            -- - event_timestamp: catches late-arriving events being backfilled
            -- - _loaded_at: would only catch newly-loaded data
            -- 
            -- We use event_timestamp because we want to catch scenarios where
            -- old events are replayed/backfilled into the source table.
            AND event_timestamp >= {{ get_incremental_lookback_timestamp('event_timestamp', 3) }}
            
            -- ADDITIONAL OPTIMIZATION: Also filter on _loaded_at
            -- This allows the query optimizer to prune partitions in the source
            -- even if they're partitioned by load date, not event date
            AND _loaded_at >= DATEADD(day, -7, CURRENT_DATE())
        {% endif %}

),

-- Deduplicate within the batch
-- Source data might have duplicates from pipeline retries
deduplicated AS (
    
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY event_id 
            ORDER BY received_at DESC  -- Keep the most recently received version
        ) AS _row_num
        
    FROM source_events
    
)

SELECT
    -- Exclude the row number helper column from final output
    {{ dbt_utils.star(from=ref('fct_clickstream_events'), except=['_row_num']) | replace('fct_clickstream_events', 'deduplicated') }}
    {# 
        NOTE: The above is a hack to get column names. In practice, just list them:
    #}
    event_id,
    user_id,
    anonymous_id,
    event_type,
    event_timestamp,
    event_date,
    event_hour,
    page_url,
    page_domain,
    page_path,
    page_section,
    referrer_url,
    referrer_domain,
    device_type,
    browser,
    country_code,
    session_id,
    product_id,
    product_category,
    search_query,
    button_name,
    event_category,
    received_at,
    _loaded_at,
    processed_at

FROM deduplicated

WHERE _row_num = 1
```

### Step 4: Add Model Documentation and Tests

Production models need documentation and tests. For incremental models, we add specific tests that catch common failure modes.

**models/marts/clickstream/_clickstream__models.yml:**

```yaml
version: 2

models:
  - name: fct_clickstream_events
    description: |
      Processed clickstream events with parsed URLs, categorized event types,
      and extracted properties. This is the primary fact table for user 
      behavior analytics.
      
      **Incremental Strategy:** 
      - Processes ~3 hours of lookback on each run
      - Uses MERGE to handle late-arriving duplicates
      - Full refresh required for schema changes or major backfills
      
      **Typical Run Time:**
      - Incremental: 8-15 minutes
      - Full refresh: 4-6 hours
      
      **Update Frequency:** Every 30 minutes via scheduled job
    
    config:
      tags: ['clickstream', 'incremental', 'tier_1']
    
    columns:
      - name: event_id
        description: "Primary key - globally unique event identifier"
        tests:
          - unique
          - not_null
      
      - name: event_timestamp
        description: "When the event occurred on the client"
        tests:
          - not_null
      
      - name: event_date
        description: "Date portion of event_timestamp, used for partitioning"
        tests:
          - not_null
      
      - name: event_category
        description: "High-level event grouping: pageview, interaction, conversion, commerce, media, other"
        tests:
          - accepted_values:
              values: ['pageview', 'interaction', 'conversion', 'commerce', 'media', 'other']
    
    tests:
      # Ensure incremental isn