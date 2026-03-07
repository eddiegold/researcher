---
title: Untangling Diverged Branches After Force Push
---

# Untangling Diverged Branches After Force Push

## Overview

Force pushes happen. Sometimes intentionally (rebasing a feature branch before merge), sometimes accidentally (a junior engineer ran the wrong command), and sometimes necessarily (removing sensitive data from history). When a teammate force-pushes to a branch you've been working on, Git will tell you that your local branch has "diverged" from the remote—leaving you with commits that exist only locally and a remote history that no longer shares a common ancestor with your work.

This walkthrough demonstrates how to:
1. Diagnose exactly what happened using Git's reflog
2. Identify which commits are "yours" versus which were on the old remote
3. Recover your work by cherry-picking onto the new remote state
4. Understand the relationship between remote-tracking branches and local refs

This is essential knowledge for any team using Git collaboratively, especially teams that rebase feature branches or maintain long-running branches with multiple contributors.

## Prerequisites

- Git 2.20+ installed
- Basic understanding of Git commits, branches, and remotes
- Familiarity with `git log` and reading commit hashes
- A terminal with Git configured (user.name, user.email set)
- Understanding that commits are immutable snapshots identified by SHA-1 hashes

To verify your Git version:
```bash
git --version
# Should output: git version 2.20.0 or higher
```

## Implementation

### Step 1: Set Up a Realistic Scenario

Let's simulate the exact situation you'd encounter in production. We'll create a repository with a shared feature branch, simulate a teammate's force push, and then your local commits that are now orphaned.

```bash
# Create a bare repository to simulate a remote (like GitHub/GitLab)
mkdir -p ~/git-recovery-demo/origin.git
cd ~/git-recovery-demo/origin.git
git init --bare

# Clone it as "teammate" (simulating your colleague)
cd ~/git-recovery-demo
git clone origin.git teammate-checkout
cd teammate-checkout
git config user.name "Alex Teammate"
git config user.email "alex@company.com"

# Create initial project structure
mkdir -p src/services
cat > src/services/payment_processor.py << 'EOF'
class PaymentProcessor:
    def __init__(self, gateway_client):
        self.gateway = gateway_client
        self.retry_count = 3
    
    def process_charge(self, amount_cents, customer_id):
        """Process a payment charge."""
        if amount_cents <= 0:
            raise ValueError("Amount must be positive")
        return self.gateway.charge(amount_cents, customer_id)
EOF

git add .
git commit -m "feat(payments): add initial PaymentProcessor class"

# Create feature branch for payment refunds
git checkout -b feature/payment-refunds
cat >> src/services/payment_processor.py << 'EOF'

    def process_refund(self, charge_id, amount_cents=None):
        """Process a full or partial refund."""
        return self.gateway.refund(charge_id, amount_cents)
EOF

git add .
git commit -m "feat(payments): add basic refund method"

# Push both branches to origin
git push -u origin main
git push -u origin feature/payment-refunds
```

Now let's set up your local checkout:

```bash
# Clone as "you" (your local development environment)
cd ~/git-recovery-demo
git clone origin.git my-checkout
cd my-checkout
git config user.name "Your Name"
git config user.email "you@company.com"

# Checkout the feature branch and add your commits
git checkout feature/payment-refunds

# Your first commit: add validation logic
cat > src/services/payment_processor.py << 'EOF'
class PaymentProcessor:
    def __init__(self, gateway_client):
        self.gateway = gateway_client
        self.retry_count = 3
        self.max_refund_cents = 100000  # $1000 limit for auto-approval
    
    def process_charge(self, amount_cents, customer_id):
        """Process a payment charge."""
        if amount_cents <= 0:
            raise ValueError("Amount must be positive")
        return self.gateway.charge(amount_cents, customer_id)

    def process_refund(self, charge_id, amount_cents=None):
        """Process a full or partial refund."""
        if amount_cents and amount_cents > self.max_refund_cents:
            raise ValueError("Refund exceeds auto-approval limit")
        return self.gateway.refund(charge_id, amount_cents)
EOF

git add .
git commit -m "feat(payments): add refund amount validation"

# Your second commit: add logging
cat > src/services/payment_processor.py << 'EOF'
import logging

logger = logging.getLogger(__name__)

class PaymentProcessor:
    def __init__(self, gateway_client):
        self.gateway = gateway_client
        self.retry_count = 3
        self.max_refund_cents = 100000  # $1000 limit for auto-approval
    
    def process_charge(self, amount_cents, customer_id):
        """Process a payment charge."""
        logger.info(f"Processing charge: {amount_cents} cents for customer {customer_id}")
        if amount_cents <= 0:
            raise ValueError("Amount must be positive")
        return self.gateway.charge(amount_cents, customer_id)

    def process_refund(self, charge_id, amount_cents=None):
        """Process a full or partial refund."""
        logger.info(f"Processing refund for charge {charge_id}")
        if amount_cents and amount_cents > self.max_refund_cents:
            raise ValueError("Refund exceeds auto-approval limit")
        return self.gateway.refund(charge_id, amount_cents)
EOF

git add .
git commit -m "feat(payments): add structured logging to payment operations"
```

Now simulate your teammate rebasing and force-pushing:

```bash
# Switch to teammate's checkout
cd ~/git-recovery-demo/teammate-checkout
git checkout feature/payment-refunds

# Teammate decides to rebase onto latest main and rewrite the refund commit
git fetch origin
git rebase origin/main

# Teammate rewrites the refund method (different implementation than original)
cat > src/services/payment_processor.py << 'EOF'
class PaymentProcessor:
    def __init__(self, gateway_client, config=None):
        self.gateway = gateway_client
        self.retry_count = 3
        self.config = config or {}
    
    def process_charge(self, amount_cents, customer_id):
        """Process a payment charge."""
        if amount_cents <= 0:
            raise ValueError("Amount must be positive")
        return self.gateway.charge(amount_cents, customer_id)

    def process_refund(self, charge_id, amount_cents=None, reason=None):
        """Process a full or partial refund with optional reason tracking."""
        metadata = {"reason": reason} if reason else {}
        return self.gateway.refund(charge_id, amount_cents, metadata=metadata)
EOF

git add .
git commit --amend -m "feat(payments): add refund method with reason tracking"

# Force push the rewritten history
git push --force origin feature/payment-refunds
```

### Step 2: Experience the Divergence

Return to your checkout and try to fetch:

```bash
cd ~/git-recovery-demo/my-checkout
git fetch origin
```

Now check the status:

```bash
git status
```

You'll see something like:
```
On branch feature/payment-refunds
Your branch and 'origin/feature/payment-refunds' have diverged,
and have 3 and 1 different commits each, respectively.
  (use "git pull" to merge the remote branch into yours)
```

**Do not run `git pull` yet.** This would create a merge commit combining both histories, which is rarely what you want. Let's understand what actually happened first.

### Step 3: Diagnose the Situation with Reflog Archaeology

The reflog is Git's safety net—it records every change to branch tips, even ones that would otherwise be lost. Let's examine what happened:

```bash
# See the history of where origin/feature/payment-refunds has pointed
git reflog show origin/feature/payment-refunds
```

Output will look like:
```
a1b2c3d origin/feature/payment-refunds@{0}: fetch origin: forced-update
e4f5g6h origin/feature/payment-refunds@{1}: fetch origin: fast-forward
```

The key insight: `forced-update` tells you the remote-tracking branch was updated in a way that wasn't a fast-forward. The old commit `e4f5g6h` is the commit your work was based on.

Now let's visualize the divergence:

```bash
# Show commits unique to your local branch (not in remote)
git log origin/feature/payment-refunds..HEAD --oneline

# Show commits unique to remote (not in your local branch)  
git log HEAD..origin/feature/payment-refunds --oneline

# See both sides together with graph
git log --oneline --graph --all --decorate
```

### Step 4: Identify Your Commits

Before recovering, you need to know exactly which commits contain your work:

```bash
# List your commits with full details
git log origin/feature/payment-refunds..HEAD --format="%H %an %s"
```

You should see your two commits:
```
abc123... Your Name feat(payments): add structured logging to payment operations
def456... Your Name feat(payments): add refund amount validation
```

Save these hashes—you'll need them:

```bash
# Store the commit range for your work
YOUR_COMMITS=$(git log origin/feature/payment-refunds..HEAD --format="%H" | tac)
echo "Your commits to recover:"
echo "$YOUR_COMMITS"
```

The `tac` reverses the order so the oldest commit is first (important for cherry-pick order).

### Step 5: Find the Original Base Commit

To understand what changed, find the last common ancestor:

```bash
# This shows where your branch originally diverged from the old remote
git merge-base HEAD origin/feature/payment-refunds@{1}
```

You can also examine the old remote state before the force push:

```bash
# See what the remote looked like before the force push
git log origin/feature/payment-refunds@{1} --oneline -5
```

### Step 6: Reset and Cherry-Pick Your Commits

Now for the recovery. We'll reset to the new remote state and replay your commits:

```bash
# First, create a backup branch in case something goes wrong
git branch backup/my-payment-work HEAD

# Reset your local branch to match the new remote state
git reset --hard origin/feature/payment-refunds

# Verify you're now at the remote's commit
git log --oneline -3
```

Now cherry-pick your commits onto the new base:

```bash
# Cherry-pick each of your commits in order
# We stored them oldest-first, so this applies them correctly
for commit in $YOUR_COMMITS; do
    echo "Cherry-picking: $commit"
    git cherry-pick "$commit"
done
```

If you encounter conflicts during cherry-pick:

```bash
# Git will stop and show you the conflict
# Edit the file to resolve, then:
git add src/services/payment_processor.py
git cherry-pick --continue

# Or if a commit is no longer relevant (perhaps the teammate included similar changes):
git cherry-pick --skip
```

### Step 7: Verify the Recovery

Confirm your commits are now on top of the teammate's changes:

```bash
# See the complete history
git log --oneline -10

# Verify the file contains both your teammate's changes AND your changes
cat src/services/payment_processor.py

# Confirm you're ahead of remote (ready to push)
git status
```

The status should show:
```
Your branch is ahead of 'origin/feature/payment-refunds' by 2 commits.
```

### Step 8: Push Your Recovered Work

```bash
# Push your commits on top of the teammate's work
git push origin feature/payment-refunds

# Clean up the backup branch
git branch -d backup/my-payment-work
```

## Running It

Execute the complete demo end-to-end:

```bash
#!/bin/bash
# save as: run_demo.sh

set -e  # Exit on any error

DEMO_DIR=~/git-recovery-demo

# Clean up any previous run
rm -rf "$DEMO_DIR"

# Run all setup steps
echo "=== Setting up demo repositories ==="
# [Insert all commands from Steps 1-2 here]

echo ""
echo "=== Demonstrating recovery ==="
cd "$DEMO_DIR/my-checkout"

# Diagnose
echo "Commits to recover:"
git log origin/feature/payment-refunds..HEAD --oneline

# Backup
git branch backup/my-payment-work HEAD

# Store commits
YOUR_COMMITS=$(git log origin/feature/payment-refunds..HEAD --format="%H" | tac)

# Reset and cherry-pick
git reset --hard origin/feature/payment-refunds

for commit in $YOUR_COMMITS; do
    git cherry-pick "$commit" || {
        echo "Conflict in $commit - resolve manually"
        exit 1
    }
done

echo ""
echo "=== Recovery complete ==="
git log --oneline -5
```

## What To Watch For

### 1. Cherry-Pick Conflicts from Overlapping Changes

If your teammate's rewritten commits touch the same lines you modified, you'll hit conflicts. When resolving:

```bash
# See what both sides changed
git diff --ours    # Your changes
git diff --theirs  # Their changes

# After resolving
git add <file>
git cherry-pick --continue
```

Don't blindly accept one side—the conflict exists because both changes matter.

### 2. Reflog Expiration

Reflog entries expire (default: 90 days for reachable commits, 30 days for unreachable). If significant time has passed since the force push:

```bash
# Check if the old ref still exists
git reflog show origin/feature/payment-refunds

# If not, check if the commit