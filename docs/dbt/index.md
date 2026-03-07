---
title: dbt - Technical Overview
tags: [dbt, data-engineering, devtools]
---

# dbt — Technical Overview

## What It Is

dbt (data build tool) is a SQL-first transformation framework that executes the "T" in ELT pipelines. It compiles SQL models with Jinja templating into dependency-ordered DAGs, then executes them against your data warehouse. dbt is **not** an orchestrator, not an ingestion tool, and not a data warehouse—it's a transformation layer that sits between your loaded raw data and your analytics-ready tables. It provides version control semantics, testing primitives, and documentation generation for SQL transformations that previously lived in ad-hoc scripts or stored procedures. dbt Core is the open-source CLI; dbt Cloud adds a managed execution environment, IDE, and scheduling—this document focuses primarily on Core.

## Core Concepts

**Models** — SQL files containing a single `SELECT` statement. The filename becomes the resulting table/view name. Models reference other models via `{{ ref('model_name') }}`, which dbt uses to infer execution order. This is the fundamental unit of work.

**Materializations** — How dbt persists model output: `view` (default, always rebuilds), `table` (full refresh), `incremental` (append/merge based on predicate), `ephemeral` (inline CTE, no persistence). Choosing wrong materializations is the primary source of cost/performance issues.

**Sources** — Declarations of external tables (raw data loaded by other tools) via `{{ source('schema', 'table') }}`. Enables freshness checks and lineage tracking without dbt managing the underlying data.

**Tests** — Assertions executed post-model-build. Generic tests (`unique`, `not_null`, `accepted_values`, `relationships`) are YAML-configured; singular tests are standalone SQL returning failing rows. Tests are **not** unit tests—they validate data state, not transformation logic.

**Macros** — Jinja functions for reusable SQL patterns. Packages like `dbt-utils` provide common macros. Overuse creates unmaintainable Jinja spaghetti; underuse leads to copy-paste proliferation.

**Profiles** — Connection configuration stored in `~/.dbt/profiles.yml` (or env vars). Maps a profile name to warehouse credentials and target schemas. Separates environment config from project code.

## Primary Use Cases

### Dimensional Modeling / Data Marts

- **When to reach for it:** Building star schemas, denormalized reporting tables, or semantic layers from normalized source data. dbt's ref-based DAG naturally expresses staging → intermediate → mart layering.
- **When NOT to reach for it:** Real-time transformations or sub-second latency requirements. dbt is batch-oriented; even incremental models have meaningful overhead.

### Data Quality Enforcement

- **When to reach for it:** Enforcing schema contracts (not_null, unique keys), referential integrity across models, and acceptable value ranges as part of CI/CD pipelines.
- **When NOT to reach for it:** Complex statistical anomaly detection or ML-based quality checks. dbt tests are SQL predicates, not statistical engines. Use Monte Carlo, Great Expectations, or similar alongside dbt, not instead of dbt tests.

### Documentation and Lineage

- **When to reach for it:** Auto-generating a browsable data catalog with column descriptions, model dependencies, and source freshness. `dbt docs generate && dbt docs serve` produces a static site with full DAG visualization.
- **When NOT to reach for it:** Enterprise data catalogs with governance workflows, PII tagging, or access control. dbt docs are read-only documentation, not a governance layer.

### Environment Parity (Dev/Staging/Prod)

- **When to reach for it:** Running identical transformation logic against different schemas/databases using target-based conditionals. Developers work in personal schemas; CI validates in staging; production runs on schedule.
- **When NOT to reach for it:** Multi-tenant architectures where tenant isolation requires dynamic schema generation at scale. dbt's target system assumes a small number of environments, not thousands.

## Senior / Staff Engineer Highlights

### Production Gotchas & Failure Modes

**1. Incremental models silently go stale.** If your `is_incremental()` predicate references a column that gets backfilled or updated retroactively, you'll accumulate data drift. The model "succeeds" but produces wrong results. Always pair incrementals with periodic full refreshes or reconciliation tests.

**2. `SELECT *` causes schema drift failures.** When upstream sources add columns, models using `SELECT *` suddenly include unexpected fields. Downstream consumers break or, worse, silently ingest garbage. Explicitly enumerate columns in production models.

**3. Single-model failures cascade unpredictably.** dbt's default behavior stops downstream models when an upstream fails. Without proper `--fail-fast` or `--warn-error` configuration, partial runs create inconsistent warehouse state. Implement idempotent models and clear retry semantics.

**4. Test failures don't block model materialization by default.** `dbt build` runs tests after models but doesn't rollback on test failure. Your table is already written with bad data. Use `dbt test --store-failures` and implement alerting, or run tests in CI before deploying model changes.

**5. Macro/package version drift across environments.** Teams pin `dbt-utils` in `packages.yml` but forget to run `dbt deps` in CI or after updates. Different package versions produce different SQL compilation. Lock versions and include `dbt deps` in every CI run.

**6. Incremental predicate scans entire table.** The `WHERE` clause in incremental models often performs a full table scan to evaluate `max(updated_at)`. At scale, this subquery becomes the bottleneck. Use warehouse-specific optimizations (clustering keys, partition pruning) or maintain state externally.

**7. Documentation rot.** YAML descriptions fall out of sync with actual model logic because there's no enforcement. Descriptions say one thing; SQL does another. Treat doc strings as code—review them in PRs.

### When NOT To Use dbt

**Real-time/streaming transformations:** dbt is batch-only. For event-time processing, use Flink, Spark Structured Streaming, or Materialize. dbt's incremental models can achieve "near real-time" (minutes) but not seconds.

**Heavy Python/ML transformations:** dbt Python models exist but are second-class citizens with limited warehouse support and awkward debugging. Use Dagster, Airflow with Python operators, or dedicated ML pipelines for anything beyond simple pandas operations.

**Complex orchestration logic:** dbt has no native branching, conditional execution, or sensor-based triggers. Pair it with Airflow, Dagster, Prefect, or dbt Cloud's scheduling for production orchestration. Don't try to encode workflow logic in Jinja.

**Multi-warehouse joins:** dbt targets a single warehouse per run. Cross-database transformations require federation layers (Trino, Starburst) or pre-landing data into a single warehouse.

**Sub-second query serving:** dbt outputs are tables/views for analytics workloads. For low-latency API backends, use a serving layer (Redis, Druid, Pinot) fed by dbt outputs, not dbt itself.

### How It Fits Into a Broader Stack

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Sources   │────▶│   Ingestion │────▶│     dbt     │────▶│  Consumers  │
│ (APIs, DBs, │     │ (Fivetran,  │     │  (Transform)│     │ (BI, ML,    │
│  files)     │     │  Airbyte,   │     │             │     │  Reverse    │
│             │     │  Stitch)    │     │             │     │  ETL)       │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                           │                   │
                           ▼                   ▼
                    ┌─────────────┐     ┌─────────────┐
                    │  Warehouse  │     │ Orchestrator│
                    │ (Snowflake, │     │ (Airflow,   │
                    │  BigQuery,  │     │  Dagster,   │
                    │  Redshift,  │     │  dbt Cloud) │
                    │  Databricks)│     └─────────────┘
                    └─────────────┘
```

**Upstream:** dbt consumes from ingestion tools (Fivetran, Airbyte, custom loaders) that land raw data into staging schemas. Define these as `sources` in dbt for freshness monitoring.

**Downstream:** BI tools (Looker, Tableau, Metabase) query dbt-produced marts. Reverse ETL tools (Census, Hightouch) sync aggregates back to operational systems. ML pipelines read feature tables dbt materializes.

**Orchestration:** Production dbt runs are triggered by Airflow DAGs, Dagster assets, Prefect flows, or dbt Cloud schedules. dbt provides the transformation graph; orchestrators handle scheduling, retries, and cross-system dependencies.

### Performance & Scale Considerations

**What breaks first:** Incremental model predicate queries. As tables grow, the `WHERE updated_at > (SELECT max(...))` pattern scans increasing data volumes. Symptoms: linearly increasing run times, warehouse timeout errors.

**Detection:** Monitor model execution times in dbt artifacts (`run_results.json`) or dbt Cloud. Alert on models exceeding historical p95 duration.

**Tuning levers:**
- **Partition pruning:** Align incremental predicates with warehouse partition columns. BigQuery partitioned tables, Snowflake clustering keys, Redshift sort keys.
- **Incremental predicates config:** Use `incremental_predicates` (dbt 1.5+) to push filters to the merge statement, avoiding full scans.
- **Parallelism:** `--threads` flag controls concurrent model execution. Warehouse concurrency limits determine ceiling.
- **Defer to prod:** `--defer --state` flags skip unchanged models by comparing manifest artifacts. Essential for CI performance.

**Warehouse-specific limits:**
- **BigQuery:** Slot contention under high concurrency; DML quotas on incremental merges.
- **Snowflake:** Warehouse auto-suspend can cause cold-start latency; micro-partitions affect clustering overhead.
- **Redshift:** Concurrent write limits; vacuum/analyze overhead on frequently updated tables.

**At scale (thousands of models):** DAG compilation itself becomes slow (minutes for very large projects). Split into multiple dbt projects with cross-project refs, or use dbt mesh architecture with project dependencies.

## Key Tradeoffs

| Aspect | Advantage | Disadvantage |
|--------|-----------|--------------|
| **SQL-first** | Analysts can contribute; leverages warehouse compute | Complex logic requires Jinja gymnastics; Python models are bolted-on |
| **Ref-based DAG** | Automatic dependency resolution; no manual ordering | Refactoring model names requires updating all downstream refs |
| **Immutable runs** | Reproducible builds via manifest/run artifacts | No native state management; incremental models require external tracking for recovery |
| **Testing as SQL** | Easy to write; executes on warehouse | Not unit tests; can't mock inputs; validates data, not logic |
| **Open-source core** | Full control; no vendor lock-in | Production features (CI, scheduling, IDE) require dbt Cloud or self-built tooling |

## Quick Reference

```bash
# Environment setup
dbt deps                          # Install packages from packages.yml
dbt debug                         # Validate connection and project config

# Development workflow
dbt compile                       # Generate compiled SQL without execution
dbt run --select my_model         # Run single model
dbt run --select my_model+        # Run model and all downstream
dbt run --select +my_model        # Run model and all upstream
dbt run --select tag:nightly      # Run models with specific tag
dbt run --exclude staging.*       # Run all except staging models

# Incremental operations
dbt run --select my_model --full-refresh  # Force full rebuild of incremental

# Testing
dbt test --select my_model        # Run tests for specific model
dbt test --select source:raw.*    # Run source freshness and tests
dbt build --select my_model       # Run + test in dependency order

# CI/CD patterns
dbt run --defer --state ./prod-artifacts --select state:modified+
dbt test --defer --state ./prod-artifacts --select state:modified+

# Documentation
dbt docs generate                 # Build documentation site
dbt docs serve                    # Local preview on port 8080

# Debugging
dbt ls --select my_model          # List models matching selector
dbt ls --resource-type test       # List all tests
dbt show --select my_model --limit 10  # Preview model output (dbt 1.5+)
```

```yaml
# Common model config patterns (schema.yml)
models:
  - name: dim_customers
    config:
      materialized: table
      tags: ['nightly', 'core']
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
      - name: status
        tests:
          - accepted_values:
              values: ['active', 'churned', 'pending']
```

```sql
-- Incremental model pattern
{{ config(
    materialized='incremental',
    unique_key='event_id',
    incremental_strategy='merge',
    on_schema_change='append_new_columns'
) }}

SELECT
    event_id,
    user_id,
    event_timestamp,
    event_type
FROM {{ source('raw', 'events') }}

{% if is_incremental() %}
WHERE event_timestamp > (SELECT max(event_timestamp) FROM {{ this }})
{% endif %}
```