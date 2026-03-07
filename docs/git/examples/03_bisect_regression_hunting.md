---
title: Automated Regression Hunting with Git Bisect
---

# Automated Regression Hunting with Git Bisect

## Overview

When your API response times suddenly jump from 45ms to 320ms somewhere in the last 200 commits, manually checking each one isn't feasible. Git bisect leverages the directed acyclic graph (DAG) structure of your commit history to perform a binary search, reducing O(n) manual investigation to O(log n) automated checks—finding the culprit in ~8 iterations instead of 200.

This walkthrough demonstrates how to build an automated bisect pipeline that:
- Defines a reproducible performance threshold test
- Handles build failures gracefully (marking commits as "skip")
- Produces actionable output identifying the exact regression commit

We'll hunt down a real performance regression in an order processing service where P95 latency degraded after a seemingly innocuous change.

## Prerequisites

**Required:**
- Git 2.25+ (`git --version`)
- A repository with linear or semi-linear history between known good/bad commits
- Bash 4.0+ for the test script
- The ability to build and run your application at arbitrary commits

**Assumed knowledge:**
- Basic git operations (checkout, log, show)
- Understanding of exit codes in shell scripts
- Familiarity with your project's build system

**Our scenario:**
- Repository: `order-processing-service`
- Known good commit: `a1b2c3d` (deployed 2024-01-15, P95 = 42ms)
- Known bad commit: `e4f5g6h` (current HEAD, P95 = 318ms)
- ~217 commits between them

## Implementation

### Step 1: Establish the Performance Baseline Test

Before automating bisect, we need a deterministic test that returns:
- Exit code `0` → commit is **good** (performance acceptable)
- Exit code `1` → commit is **bad** (regression present)
- Exit code `125` → commit is **untestable** (skip it)

Create `scripts/bisect-perf-test.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Configuration — adjust these for your system
readonly SERVICE_NAME="order-processing-service"
readonly THRESHOLD_MS=100          # P95 must be below this
readonly WARMUP_REQUESTS=50        # Requests before measuring
readonly TEST_REQUESTS=200         # Requests to measure
readonly SERVICE_PORT=8080
readonly STARTUP_TIMEOUT_SEC=30

# Paths
readonly PROJECT_ROOT="$(git rev-parse --show-toplevel)"
readonly LOG_DIR="${PROJECT_ROOT}/.bisect-logs"
readonly CURRENT_SHA="$(git rev-parse --short HEAD)"

mkdir -p "${LOG_DIR}"

log() {
    echo "[bisect-test] $(date '+%H:%M:%S') $*" | tee -a "${LOG_DIR}/${CURRENT_SHA}.log"
}

cleanup() {
    log "Cleaning up..."
    # Kill any running instance of our service
    pkill -f "${SERVICE_NAME}" 2>/dev/null || true
    # Clean up test containers if using Docker
    docker rm -f "${SERVICE_NAME}-test" 2>/dev/null || true
}

trap cleanup EXIT

# Step 1: Attempt to build the project
build_project() {
    log "Building at commit ${CURRENT_SHA}..."
    
    # Handle projects that changed build systems mid-history
    if [[ -f "pom.xml" ]]; then
        mvn clean package -DskipTests -q 2>>"${LOG_DIR}/${CURRENT_SHA}.log"
    elif [[ -f "build.gradle" ]]; then
        ./gradlew clean build -x test -q 2>>"${LOG_DIR}/${CURRENT_SHA}.log"
    elif [[ -f "go.mod" ]]; then
        go build -o "bin/${SERVICE_NAME}" ./cmd/server 2>>"${LOG_DIR}/${CURRENT_SHA}.log"
    else
        log "ERROR: Unknown build system"
        return 1
    fi
}

# Step 2: Start the service and wait for health
start_service() {
    log "Starting service..."
    
    # Adjust this command to match your service's startup
    ./bin/${SERVICE_NAME} \
        --port=${SERVICE_PORT} \
        --config=config/test.yaml \
        --log-level=warn \
        &>"${LOG_DIR}/${CURRENT_SHA}-runtime.log" &
    
    local pid=$!
    echo "${pid}" > "${LOG_DIR}/service.pid"
    
    # Wait for health endpoint
    local elapsed=0
    while (( elapsed < STARTUP_TIMEOUT_SEC )); do
        if curl -sf "http://localhost:${SERVICE_PORT}/health" >/dev/null 2>&1; then
            log "Service healthy after ${elapsed}s"
            return 0
        fi
        sleep 1
        ((elapsed++))
    done
    
    log "ERROR: Service failed to start within ${STARTUP_TIMEOUT_SEC}s"
    return 1
}

# Step 3: Run the performance test
measure_p95_latency() {
    log "Running warmup (${WARMUP_REQUESTS} requests)..."
    
    # Warmup phase — let JIT compile, caches fill, etc.
    for ((i=0; i<WARMUP_REQUESTS; i++)); do
        curl -sf -X POST "http://localhost:${SERVICE_PORT}/api/v1/orders/validate" \
            -H "Content-Type: application/json" \
            -d '{"customer_id":"cust_12345","items":[{"sku":"WIDGET-001","quantity":2}]}' \
            >/dev/null 2>&1 || true
    done
    
    log "Measuring latency (${TEST_REQUESTS} requests)..."
    
    # Collect latency samples
    local latencies=()
    for ((i=0; i<TEST_REQUESTS; i++)); do
        # curl's -w flag gives us precise timing in milliseconds
        local latency_ms
        latency_ms=$(curl -sf -X POST "http://localhost:${SERVICE_PORT}/api/v1/orders/validate" \
            -H "Content-Type: application/json" \
            -d '{"customer_id":"cust_12345","items":[{"sku":"WIDGET-001","quantity":2}]}' \
            -w "%{time_total}" \
            -o /dev/null 2>/dev/null)
        
        # Convert seconds to milliseconds
        latency_ms=$(echo "${latency_ms} * 1000" | bc)
        latencies+=("${latency_ms}")
    done
    
    # Calculate P95 — sort and take the 95th percentile value
    local sorted_latencies
    sorted_latencies=$(printf '%s\n' "${latencies[@]}" | sort -n)
    
    local p95_index=$(( (TEST_REQUESTS * 95) / 100 ))
    local p95_value
    p95_value=$(echo "${sorted_latencies}" | sed -n "${p95_index}p")
    
    # Round to integer for comparison
    p95_value=$(printf "%.0f" "${p95_value}")
    
    log "P95 latency: ${p95_value}ms (threshold: ${THRESHOLD_MS}ms)"
    echo "${p95_value}"
}

# Main execution
main() {
    log "========================================"
    log "Testing commit: ${CURRENT_SHA}"
    log "========================================"
    
    # Build phase — exit 125 (skip) if build fails
    # This handles commits with syntax errors, missing deps, etc.
    if ! build_project; then
        log "Build failed — marking commit as SKIP"
        exit 125  # Special exit code tells bisect to skip this commit
    fi
    
    # Startup phase — also skip if service won't start
    if ! start_service; then
        log "Service startup failed — marking commit as SKIP"
        exit 125
    fi
    
    # Measurement phase
    local p95
    p95=$(measure_p95_latency)
    
    # Decision: is this commit good or bad?
    if (( p95 < THRESHOLD_MS )); then
        log "RESULT: GOOD (${p95}ms < ${THRESHOLD_MS}ms)"
        exit 0
    else
        log "RESULT: BAD (${p95}ms >= ${THRESHOLD_MS}ms)"
        exit 1
    fi
}

main "$@"
```

### Step 2: Validate the Test Script at Boundaries

Before running bisect, verify the script correctly identifies both endpoints:

```bash
# Test at the known-good commit
git checkout a1b2c3d
./scripts/bisect-perf-test.sh
echo "Exit code: $?"  # Should be 0

# Test at the known-bad commit  
git checkout e4f5g6h
./scripts/bisect-perf-test.sh
echo "Exit code: $?"  # Should be 1

# Return to main branch
git checkout main
```

### Step 3: Configure Bisect for Complex History

For repositories with merge commits, configure bisect behavior:

```bash
# Create a bisect configuration that handles your branching model
cat > .git/info/bisect-options << 'EOF'
# Skip merge commits — they often have build issues and 
# the actual change is in the merged branch anyway
GIT_BISECT_SKIP_MERGE=1
EOF
```

### Step 4: Run the Automated Bisect

```bash
# Start bisect session
git bisect start

# Mark the boundaries
git bisect bad e4f5g6h   # Current HEAD with regression
git bisect good a1b2c3d  # Last known good deployment

# Launch automated binary search
# Git will checkout commits and run your script until it finds the first bad commit
git bisect run ./scripts/bisect-perf-test.sh
```

### Step 5: Analyze the Results

When bisect completes, you'll see output like:

```
e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6 is the first bad commit
commit e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6
Author: jane.doe@company.com
Date:   Tue Jan 23 14:32:17 2024 -0800

    perf: Switch order validation to use reflection-based field mapping
    
    This simplifies the validation logic by dynamically mapping fields
    instead of explicit accessors.
    
    Closes #1847

 src/main/java/com/company/orders/validation/OrderValidator.java | 47 +++++-----
 1 file changed, 28 insertions(+), 19 deletions(-)
```

Create a summary script to generate a report:

```bash
#!/usr/bin/env bash
# scripts/bisect-report.sh — Generate a summary after bisect completes

readonly FIRST_BAD=$(git bisect log | grep "first bad commit" | awk '{print $1}')
readonly LOG_DIR="$(git rev-parse --show-toplevel)/.bisect-logs"

echo "=== BISECT REGRESSION REPORT ==="
echo ""
echo "First bad commit: ${FIRST_BAD}"
echo ""
echo "Commit details:"
git show --stat "${FIRST_BAD}"
echo ""
echo "Files changed:"
git diff-tree --no-commit-id --name-only -r "${FIRST_BAD}"
echo ""
echo "Performance measurements collected:"
echo "Commit       | P95 (ms) | Result"
echo "-------------|----------|-------"

for logfile in "${LOG_DIR}"/*.log; do
    if [[ -f "${logfile}" ]]; then
        sha=$(basename "${logfile}" .log)
        p95=$(grep "P95 latency:" "${logfile}" 2>/dev/null | tail -1 | grep -oE '[0-9]+ms' | tr -d 'ms')
        result=$(grep "RESULT:" "${logfile}" 2>/dev/null | tail -1 | awk '{print $2}')
        [[ -n "${p95}" ]] && printf "%-12s | %8s | %s\n" "${sha}" "${p95}" "${result}"
    fi
done | sort -t'|' -k2 -n

# Clean up bisect state
echo ""
echo "Run 'git bisect reset' to return to your original HEAD"
```

## Running It

Complete end-to-end execution:

```bash
# 1. Clone and enter the repository
cd ~/projects/order-processing-service

# 2. Ensure the test script is executable
chmod +x scripts/bisect-perf-test.sh
chmod +x scripts/bisect-report.sh

# 3. Verify test script works at both boundaries
git stash  # Save any local changes

git checkout a1b2c3d
./scripts/bisect-perf-test.sh && echo "✓ Good commit verified"

git checkout e4f5g6h  
./scripts/bisect-perf-test.sh || echo "✓ Bad commit verified"

# 4. Run automated bisect (this takes ~15-30 minutes for 200 commits)
git bisect start --no-checkout  # Optional: avoid modifying working tree
git bisect bad e4f5g6h
git bisect good a1b2c3d
time git bisect run ./scripts/bisect-perf-test.sh

# 5. Generate the report
./scripts/bisect-report.sh > regression-report-$(date +%Y%m%d).txt

# 6. Clean up
git bisect reset
git stash pop  # Restore local changes
```

Expected output:

```
running ./scripts/bisect-perf-test.sh
[bisect-test] 14:23:01 ========================================
[bisect-test] 14:23:01 Testing commit: f3d4e5a
[bisect-