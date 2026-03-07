---
title: Writing Cross-Database Macros with Adapter Dispatching
---

# Writing Cross-Database Macros with Adapter Dispatching

## Overview

When your organization runs dbt across multiple data warehouses—perhaps Snowflake for your core analytics, BigQuery for a recently acquired subsidiary, and Redshift for legacy systems—you face a fundamental problem: SQL dialects diverge in frustrating ways. Date functions, string manipulation, and even basic syntax differ enough that a macro working perfectly on Snowflake will fail spectacularly on BigQuery.

dbt's adapter dispatch pattern solves this by letting you write a single macro interface that routes to warehouse-specific implementations at runtime. This walkthrough builds two production macros that demonstrate the pattern: a **date spine generator** (essential for filling gaps in time-series data) and a **dynamic pivot macro** (for transforming rows to columns without hardcoding values).

By the end, you'll have macros that:
- Automatically detect the running warehouse and execute appropriate SQL
- Handle edge cases specific to each platform
- Remain maintainable as you add new warehouse support

## Prerequisites

**Required:**
- dbt Core 1.5+ or dbt Cloud
- Access to at least one of: Snowflake, BigQuery, or Redshift
- Familiarity with dbt macros and Jinja templating

**Helpful:**
- Understanding of CTEs and window functions
- Experience with at least two SQL dialects

**Project Setup:**
Your `dbt_project.yml` should have a macro-paths configuration (default is fine):

```yaml
# dbt_project.yml
name: 'analytics'
version: '1.0.0'
config-version: 2

macro-paths: ["macros"]
```

## Implementation

### Step 1: Understanding the Dispatch Pattern

Before writing code, let's understand how adapter dispatch works. When you call a macro with dispatch, dbt searches for implementations in this order:

1. `<your_project>__<macro_name>` (your project-specific override)
2. `<macro_namespace>_<adapter_type>__<macro_name>` (adapter-specific)
3. `<macro_namespace>__<macro_name>` (default fallback)

This means we'll create:
- A main macro that calls `adapter.dispatch()`
- Adapter-specific implementations suffixed with the adapter name
- A default implementation for unsupported warehouses

### Step 2: Building the Date Spine Generator

A date spine is a table containing one row per date in a range. It's critical for time-series analysis where you need to show zero values for days with no activity rather than missing rows entirely.

Create `macros/cross_db/generate_date_spine.sql`:

```sql
{# 
    Main entry point for date spine generation.
    This macro dispatches to warehouse-specific implementations.
    
    Arguments:
        start_date: The beginning of the date range (inclusive)
        end_date: The end of the date range (inclusive)  
        date_column_name: What to name the output date column (default: 'date_day')
#}

{% macro generate_date_spine(start_date, end_date, date_column_name='date_day') %}
    {{ return(adapter.dispatch('generate_date_spine', 'analytics')(start_date, end_date, date_column_name)) }}
{% endmacro %}


{# ============================================
   SNOWFLAKE IMPLEMENTATION
   Uses GENERATOR with ROWCOUNT for efficiency
   ============================================ #}

{% macro snowflake__generate_date_spine(start_date, end_date, date_column_name) %}

    {# Snowflake's GENERATOR is the most efficient approach here.
       We calculate the day difference to know how many rows to generate,
       then use DATEADD to create each date. #}
    
    with date_spine as (
        select
            dateadd(
                day,
                row_number() over (order by null) - 1,  -- ROW_NUMBER is 1-based, so subtract 1
                {{ start_date }}::date
            ) as {{ date_column_name }}
        from table(
            generator(
                rowcount => datediff(day, {{ start_date }}::date, {{ end_date }}::date) + 1
            )
        )
    )
    
    select {{ date_column_name }}
    from date_spine
    where {{ date_column_name }} <= {{ end_date }}::date  -- Safety bound in case of edge cases

{% endmacro %}


{# ============================================
   BIGQUERY IMPLEMENTATION  
   Uses GENERATE_DATE_ARRAY for native support
   ============================================ #}

{% macro bigquery__generate_date_spine(start_date, end_date, date_column_name) %}

    {# BigQuery has native date array generation - use it!
       UNNEST flattens the array into rows. #}
    
    select
        date_value as {{ date_column_name }}
    from unnest(
        generate_date_array(
            cast({{ start_date }} as date),
            cast({{ end_date }} as date),
            interval 1 day
        )
    ) as date_value

{% endmacro %}


{# ============================================
   REDSHIFT IMPLEMENTATION
   Uses recursive CTE (no generator function)
   ============================================ #}

{% macro redshift__generate_date_spine(start_date, end_date, date_column_name) %}

    {# Redshift lacks generator functions, so we use a recursive CTE.
       Note: Redshift has a max recursion depth, but for reasonable date ranges
       (up to ~10 years / 3650 days) this is fine. For longer ranges,
       consider a cross-join approach with a numbers table. #}
    
    with recursive date_spine as (
        -- Anchor: start with the first date
        select 
            {{ start_date }}::date as {{ date_column_name }}
        
        union all
        
        -- Recursive: add one day until we hit the end
        select 
            dateadd(day, 1, {{ date_column_name }})
        from date_spine
        where {{ date_column_name }} < {{ end_date }}::date
    )
    
    select {{ date_column_name }}
    from date_spine

{% endmacro %}


{# ============================================
   DEFAULT IMPLEMENTATION (Postgres-compatible)
   Falls back for DuckDB, Postgres, etc.
   ============================================ #}

{% macro default__generate_date_spine(start_date, end_date, date_column_name) %}

    {# generate_series works on Postgres and DuckDB.
       This serves as our fallback for adapters we haven't explicitly handled. #}
    
    select 
        generated_date::date as {{ date_column_name }}
    from generate_series(
        {{ start_date }}::date,
        {{ end_date }}::date,
        interval '1 day'
    ) as t(generated_date)

{% endmacro %}
```

**Key decisions explained:**

1. **Why dispatch with a namespace?** The second argument `'analytics'` is your project name. This allows other projects to override your implementations if needed.

2. **Why different approaches per warehouse?** Each warehouse has an optimal pattern. Snowflake's `GENERATOR` is a single table scan. BigQuery's `GENERATE_DATE_ARRAY` is purpose-built. Redshift requires recursion because it lacks generator functions.

3. **Why the safety bound on Snowflake?** The `GENERATOR` function occasionally produces one extra row due to how `DATEDIFF` calculates intervals at day boundaries. The `WHERE` clause costs nothing but prevents subtle bugs.

### Step 3: Building the Dynamic Pivot Macro

Pivoting transforms rows into columns—turning this:

| customer_id | metric_name | value |
|-------------|-------------|-------|
| 1001        | revenue     | 500   |
| 1001        | orders      | 3     |

Into this:

| customer_id | revenue | orders |
|-------------|---------|--------|
| 1001        | 500     | 3      |

The challenge: SQL's `PIVOT` syntax differs wildly between warehouses, and some don't support it natively at all.

Create `macros/cross_db/pivot_values.sql`:

```sql
{#
    Dynamic pivot macro that transforms rows to columns.
    
    Arguments:
        source_relation: The ref() or source() to pivot
        group_by_columns: List of columns to group by (preserved in output)
        pivot_column: The column whose distinct values become new column names
        value_column: The column containing values to aggregate
        agg_function: Aggregation function to apply (default: 'sum')
        value_list: Optional explicit list of pivot values. If not provided,
                    macro will query distinct values (requires run-time query)
        quote_identifiers: Whether to quote the generated column names (default: true)
#}

{% macro pivot_values(
    source_relation,
    group_by_columns,
    pivot_column,
    value_column,
    agg_function='sum',
    value_list=none,
    quote_identifiers=true
) %}
    {{ return(adapter.dispatch('pivot_values', 'analytics')(
        source_relation,
        group_by_columns,
        pivot_column,
        value_column,
        agg_function,
        value_list,
        quote_identifiers
    )) }}
{% endmacro %}


{# ============================================
   SNOWFLAKE IMPLEMENTATION
   Uses native PIVOT syntax
   ============================================ #}

{% macro snowflake__pivot_values(
    source_relation,
    group_by_columns,
    pivot_column,
    value_column,
    agg_function,
    value_list,
    quote_identifiers
) %}

    {# If no value_list provided, we need to query for distinct values.
       This happens at compile time via run_query. #}
    {% if value_list is none %}
        {% set value_query %}
            select distinct {{ pivot_column }}
            from {{ source_relation }}
            where {{ pivot_column }} is not null
            order by 1
        {% endset %}
        
        {% set results = run_query(value_query) %}
        {% set value_list = results.columns[0].values() %}
    {% endif %}

    {# Snowflake's PIVOT is clean but requires explicit value listing #}
    select
        {% for col in group_by_columns %}
            {{ col }},
        {% endfor %}
        {% for val in value_list %}
            {% set col_name = val | string | replace("'", "") | replace(" ", "_") | lower %}
            {% if quote_identifiers %}
                "{{ col_name }}"
            {% else %}
                {{ col_name }}
            {% endif %}
            {%- if not loop.last %},{% endif %}
        {% endfor %}
    from {{ source_relation }}
    pivot (
        {{ agg_function }}({{ value_column }})
        for {{ pivot_column }} in (
            {% for val in value_list %}
                '{{ val }}'
                {%- if not loop.last %},{% endif %}
            {% endfor %}
        )
    ) as pivoted (
        {% for col in group_by_columns %}
            {{ col }},
        {% endfor %}
        {% for val in value_list %}
            {% set col_name = val | string | replace("'", "") | replace(" ", "_") | lower %}
            {% if quote_identifiers %}
                "{{ col_name }}"
            {% else %}
                {{ col_name }}
            {% endif %}
            {%- if not loop.last %},{% endif %}
        {% endfor %}
    )

{% endmacro %}


{# ============================================
   BIGQUERY IMPLEMENTATION
   Uses PIVOT syntax (GA since 2021)
   ============================================ #}

{% macro bigquery__pivot_values(
    source_relation,
    group_by_columns,
    pivot_column,
    value_column,
    agg_function,
    value_list,
    quote_identifiers
) %}

    {% if value_list is none %}
        {% set value_query %}
            select distinct {{ pivot_column }}
            from {{ source_relation }}
            where {{ pivot_column }} is not null
            order by 1
        {% endset %}
        
        {% set results = run_query(value_query) %}
        {% set value_list = results.columns[0].values() %}
    {% endif %}

    {# BigQuery's PIVOT syntax is slightly different - no alias block needed #}
    select *
    from (
        select
            {% for col in group_by_columns %}
                {{ col }},
            {% endfor %}
            {{ pivot_column }},
            {{ value_column }}
        from {{ source_relation }}
    )
    pivot (
        {{ agg_function }}({{ value_column }})
        for {{ pivot_column }} in (
            {% for val in value_list %}
                '{{ val }}'
                {%- if not loop.last %},{% endif %}
            {% endfor %}
        )
    )

{% endmacro %}


{# ============================================
   REDSHIFT IMPLEMENTATION
   No native PIVOT - uses conditional aggregation
   ============================================ #}

{% macro redshift__pivot_values(
    source_relation,
    group_by_columns,
    pivot_column,
    value_column,
    agg_function,
    value_list,
    quote_identifiers
) %}

    {% if value_list is none %}
        {% set value_query %}
            select distinct {{ pivot_column }}
            from {{ source_relation }}
            where {{ pivot_column }} is not null
            order by 1
        {% endset %}
        
        {% set results = run_query(value_query) %}
        {% set value_list = results.columns[0].values() %}
    {% endif %}

    {# Redshift doesn't have PIVOT, so we use the classic CASE-based approach.
       This is actually what PIVOT compiles to internally on most databases. #}
    
    select
        {% for col in group_by_columns %}
            {{ col }},
        {% endfor %}
        {% for val in value_list %}
            {% set col_name