---
title: Extracting a Service from a Monorepo with Filter-Repo
---

# Extracting a Service from a Monorepo with Filter-Repo

## Overview

When an engineering organization matures, monorepos often need to be decomposed into independent repositories. This might happen when a team needs independent deployment cycles, when you're spinning off a service to a separate team, or when the monorepo has grown unwieldy for certain workflows.

The challenge isn't just copying files—it's preserving commit history, maintaining author attribution, rewriting paths so the extracted service becomes the repository root, and cleaning up references to unrelated code. `git filter-repo` is the modern replacement for `git filter-branch`, offering dramatically better performance and a cleaner interface for these repository surgery operations.

This walkthrough demonstrates extracting a `payments-service` from a monorepo structured like:

```
platform/
├── services/
│   ├── payments-service/
│   ├── user-service/
│   └── notification-service/
├── libs/
│   └── shared-utils/
├── infrastructure/
└── docs/
```

We'll extract `payments-service` into its own repository where `services/payments-service/` becomes the root, all irrelevant history is removed, and commits touching only other services disappear entirely.

## Prerequisites

**Software Requirements:**
- Git 2.22+ (for `git filter-repo` compatibility)
- Python 3.5+ (filter-repo is a Python script)
- `git-filter-repo` installed:
  ```bash
  # macOS
  brew install git-filter-repo
  
  # pip (any platform)
  pip3 install git-filter-repo
  
  # Verify installation
  git filter-repo --version
  ```

**Repository Requirements:**
- Push access to create the new target repository
- A clean clone of the monorepo (we'll be rewriting history)
- No uncommitted changes in your working copy

**Knowledge Assumptions:**
- Familiarity with Git fundamentals (commits, refs, remotes)
- Understanding of your monorepo's directory structure
- Awareness of any path-based CI/CD triggers that may need updating

## Implementation

### Step 1: Create a Fresh Clone for Surgery

Never perform filter-repo operations on your working copy or a repository with valuable local state. Always start with a fresh clone.

```bash
# Clone with full history but without checking out a working tree yet
# Using --no-checkout speeds up the initial clone for large repos
git clone --no-checkout git@github.com:acme-corp/platform-monorepo.git payments-service-extraction

cd payments-service-extraction

# Now check out the default branch
git checkout main

# Verify you're on a fresh clone with no remotes we care about preserving
git remote -v
# origin  git@github.com:acme-corp/platform-monorepo.git (fetch)
# origin  git@github.com:acme-corp/platform-monorepo.git (push)
```

**Why a fresh clone?** `git filter-repo` rewrites commit SHAs. If you run this on a shared clone, you'll create divergent history that can't be reconciled with the original. The fresh clone is disposable—if something goes wrong, delete it and start over.

### Step 2: Analyze the Directory Structure

Before extracting, understand exactly what you're pulling out and what dependencies exist.

```bash
# List the directory structure of the service we want to extract
find services/payments-service -type f -name "*.go" | head -20

# Check for imports from other parts of the monorepo
# This helps identify if we need to include additional paths
grep -r "acme-corp/platform-monorepo/libs" services/payments-service/ || echo "No shared lib imports found"

# Count commits touching this directory (gives you a sense of history size)
git log --oneline -- services/payments-service | wc -l

# Identify contributors to this service (for attribution verification later)
git shortlog -sn -- services/payments-service
```

For our example, let's say we find:
- 847 commits touch `services/payments-service`
- The service imports from `libs/shared-utils/pkg/money` (a currency handling library)
- 12 unique contributors have worked on this service

### Step 3: Run the Initial Extraction

The core operation uses `--subdirectory-filter` equivalent functionality through `--path`:

```bash
# Extract only the payments-service directory
# --path specifies which paths to keep
# --path-rename moves the extracted path to repository root
git filter-repo \
    --path services/payments-service/ \
    --path-rename services/payments-service/:

# Verify the extraction worked
ls -la
# Should now show contents of what was services/payments-service/ at root:
# cmd/
# internal/
# pkg/
# go.mod
# go.sum
# Dockerfile
# README.md
```

**What just happened:**
1. Every commit not touching `services/payments-service/` was removed
2. Remaining commits were rewritten with new SHAs
3. The path prefix `services/payments-service/` was stripped from all file paths
4. Empty merge commits were removed by default
5. The original remote was removed (filter-repo does this intentionally as a safety measure)

### Step 4: Include Shared Dependencies (If Needed)

If the service depends on shared libraries that should travel with it, you need a more complex extraction:

```bash
# Start over with a fresh clone for this approach
cd ..
rm -rf payments-service-extraction
git clone git@github.com:acme-corp/platform-monorepo.git payments-service-extraction
cd payments-service-extraction

# Extract multiple paths, keeping their relative structure first
git filter-repo \
    --path services/payments-service/ \
    --path libs/shared-utils/pkg/money/
```

Now we need to reorganize the directory structure. Create a path mapping file:

```bash
# Create a file specifying path transformations
cat > /tmp/path-mappings.txt << 'EOF'
# Move the service to root, keep shared lib in a vendor-like location
regex:^services/payments-service/(.*)$==>\1
regex:^libs/shared-utils/(.*)$==>vendor/acme-internal/\1
EOF

# Apply the path rewriting
git filter-repo --paths-from-file /tmp/path-mappings.txt --replace-refs delete-no-add
```

**Why this two-step approach?** Filter-repo's `--path` and `--path-rename` options don't compose well for complex restructuring. Using a paths file with regex gives precise control over where each extracted component lands.

### Step 5: Clean Up Commit Messages

Monorepo commits often reference ticket systems with paths or other services. Let's clean those up:

```python
#!/usr/bin/env python3
# Save as /tmp/message-rewriter.py

import re
import sys

def rewrite_message(message):
    # Remove path prefixes from commit messages that referenced the old location
    message = re.sub(
        r'services/payments-service/', 
        '', 
        message
    )
    
    # Update any internal references to the monorepo structure
    message = re.sub(
        r'platform-monorepo#(\d+)',
        r'platform-monorepo#\1 (historical reference)',
        message
    )
    
    # Preserve JIRA/ticket references as-is (PAY-123 style)
    # No modification needed, but this is where you'd transform if needed
    
    return message

# git filter-repo calls this with message as argument
if __name__ == '__main__':
    original = sys.stdin.buffer.read()
    modified = rewrite_message(original.decode('utf-8'))
    sys.stdout.buffer.write(modified.encode('utf-8'))
```

```bash
# Apply the message callback
chmod +x /tmp/message-rewriter.py

git filter-repo \
    --message-callback '
        message = message.decode("utf-8")
        message = message.replace("services/payments-service/", "")
        return message.encode("utf-8")
    '
```

### Step 6: Verify Author Attribution

Ensure contributor information survived the extraction intact:

```bash
# Compare before and after (you saved this from Step 2)
git shortlog -sn

# Check for any commits with missing author info
git log --format='%ae %an' | sort -u

# Verify GPG signatures are noted (they'll be invalid after rewrite, but should be preserved as historical record)
git log --show-signature -1 2>&1 | head -5
```

If your organization uses a mailmap, apply it now:

```bash
# Create a mailmap for email normalization
cat > .mailmap << 'EOF'
Jane Developer <jane.developer@acme-corp.com> <jane.d@old-domain.com>
Jane Developer <jane.developer@acme-corp.com> <jdeveloper@contractor.com>
EOF

git filter-repo --mailmap .mailmap
```

### Step 7: Set Up the New Remote and Push

```bash
# Create the new repository on GitHub/GitLab first (via UI or CLI)
# gh repo create acme-corp/payments-service --private

# Add the new remote
git remote add origin git@github.com:acme-corp/payments-service.git

# Push all branches and tags
git push --set-upstream origin main
git push --all origin
git push --tags origin

# Verify the push
git log --oneline -10 origin/main
```

### Step 8: Create Tombstone in Original Monorepo

Back in the original monorepo, mark the extraction clearly:

```bash
cd ../platform-monorepo  # Your normal working copy

# Create a tombstone file
cat > services/payments-service/EXTRACTED.md << 'EOF'
# ⚠️ This Service Has Been Extracted

**Extraction Date:** 2024-01-15
**New Repository:** https://github.com/acme-corp/payments-service
**Extraction Commit:** abc123def (in this repo)

This directory is preserved as a tombstone to:
1. Prevent accidental recreation of this path
2. Document the migration for historical context
3. Provide redirect information for developers

For CI/CD purposes, changes to this directory should be ignored.
EOF

# Remove the actual code but keep the tombstone
git rm -r services/payments-service/cmd services/payments-service/internal services/payments-service/pkg
git add services/payments-service/EXTRACTED.md
git commit -m "chore: extract payments-service to dedicated repository

The payments-service has been extracted to its own repository
for independent deployment and team autonomy.

New location: https://github.com/acme-corp/payments-service

History was preserved using git-filter-repo."

git push origin main
```

## Running It

Here's the complete extraction as a single executable script:

```bash
#!/bin/bash
set -euo pipefail

# Configuration
MONOREPO_URL="git@github.com:acme-corp/platform-monorepo.git"
SERVICE_PATH="services/payments-service"
NEW_REPO_URL="git@github.com:acme-corp/payments-service.git"
WORK_DIR=$(mktemp -d)

echo "Working in: $WORK_DIR"
cd "$WORK_DIR"

# Step 1: Fresh clone
echo ">>> Cloning monorepo..."
git clone "$MONOREPO_URL" extraction
cd extraction

# Step 2: Record pre-extraction stats
echo ">>> Recording pre-extraction statistics..."
COMMIT_COUNT=$(git log --oneline -- "$SERVICE_PATH" | wc -l | tr -d ' ')
CONTRIBUTOR_COUNT=$(git shortlog -sn -- "$SERVICE_PATH" | wc -l | tr -d ' ')
echo "Found $COMMIT_COUNT commits from $CONTRIBUTOR_COUNT contributors"

# Step 3: Extract
echo ">>> Extracting $SERVICE_PATH..."
git filter-repo \
    --path "$SERVICE_PATH/" \
    --path-rename "$SERVICE_PATH/:"

# Step 4: Verify extraction
echo ">>> Verifying extraction..."
POST_COMMIT_COUNT=$(git log --oneline | wc -l | tr -d ' ')
echo "Extracted repository has $POST_COMMIT_COUNT commits"

if [ "$POST_COMMIT_COUNT" -eq 0 ]; then
    echo "ERROR: No commits survived extraction. Check the SERVICE_PATH."
    exit 1
fi

# Step 5: Push to new repository
echo ">>> Pushing to new repository..."
git remote add origin "$NEW_REPO_URL"
git push --set-upstream origin main --force
git push --tags origin

echo ">>> Extraction complete!"
echo "New repository: $NEW_REPO_URL"
echo "Working directory preserved at: $WORK_DIR"
```

Execute with:

```bash
chmod +x extract-service.sh
./extract-service.sh
```

## What To Watch For

### 1. Empty Commits and Merge Commits

Filter-repo removes empty commits by default, but merge commits that become empty (because they merged changes outside your extracted path) can leave confusing history.

```bash
# Check for merge commits that might look orphaned
git log --oneline --merges | head -20

# If you see confusing merges, you can flatten history (destructive!)
git filter-repo --prune-empty always --prune-degenerate always
```

### 2. Submodule References

If your monorepo contained submodules, and any paths within submodules overlapped with your extraction pattern, you'll get corrupted history.

```bash
# Check for submodules before extraction
git submodule status

# If submodules exist, you may need to deinit them first or exclude their paths
```

### 3. Large Files and Git LFS

Files tracked by Git LFS will have their pointer files extracted, but you'll need to ensure LFS is configured in the new repository and that you have access to the LFS storage.

```bash
# Check for LFS files in the extracted content
git lfs ls-files

# If LFS files exist, ensure tracking rules are in place
cat .gitattributes
```

### 4. Branch and Tag Contamination

All branches and tags are processed by filter-repo. If a branch never touched your extracted path, it will become empty and may cause push failures.

```bash
# List branches that survived with commits
for branch in