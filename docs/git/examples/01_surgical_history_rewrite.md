---
title: Surgical History Rewriting with Interactive Rebase
---

# Surgical History Rewriting with Interactive Rebase

## Overview

When working on a feature branch, developers often accumulate a messy commit history: hasty WIP saves, typo fixes, forgotten files, and debugging commits that never got cleaned up. Before opening a pull request, you want a clean, logical history that tells a story reviewers can follow.

Interactive rebase (`git rebase -i`) lets you rewrite history by squashing commits together, reordering them, editing messages, or even splitting commits apart. Understanding this operation requires grasping Git's "three trees" model:

1. **Working Directory** — the actual files on disk
2. **Staging Area (Index)** — what's queued for the next commit  
3. **HEAD (Repository)** — the current commit snapshot

When you rebase, Git replays commits onto a new base. Because each commit's hash is derived from its content *and* its parent hash, changing anything upstream causes all downstream commits to get new SHA-1 identifiers. This is why you should never rebase commits that others have based work on.

This walkthrough demonstrates cleaning up a realistic feature branch for a payment processing service before code review.

## Prerequisites

- Git 2.23+ installed (for modern interactive rebase features)
- Basic familiarity with Git commits, branches, and diffs
- A terminal with a configured text editor (`$EDITOR` or `core.editor`)
- Understanding that rewriting history changes commit hashes

Configure your editor if not already set:

```bash
git config --global core.editor "vim"  # or code --wait, nano, etc.
```

## Implementation

### Step 1: Set Up a Realistic Feature Branch

Let's create a repository simulating a payment gateway integration with a messy development history.

```bash
# Create and initialize the repository
mkdir payment-service && cd payment-service
git init

# Create initial project structure (simulating existing main branch)
cat > payment_gateway.py << 'EOF'
"""Payment gateway abstraction layer."""

class PaymentGateway:
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    def charge(self, amount_cents: int, currency: str) -> dict:
        raise NotImplementedError("Subclasses must implement charge()")
EOF

cat > requirements.txt << 'EOF'
requests>=2.28.0
pydantic>=2.0.0
EOF

git add .
git commit -m "Initial payment gateway interface"
```

### Step 2: Simulate Messy Feature Development

Now we'll create the kind of commit history that accumulates during real development — WIP commits, forgotten files, typo fixes, and debug code:

```bash
# Start feature branch for Stripe integration
git checkout -b feature/stripe-integration

# Commit 1: Start implementing Stripe gateway (incomplete)
cat > stripe_gateway.py << 'EOF'
"""Stripe payment gateway implementation."""
import requests

class StripeGateway:
    BASE_URL = "https://api.stripe.com/v1"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    def charge(self, amount_cents: int, currency: str):
        # TODO: implement this
        pass
EOF

git add stripe_gateway.py
git commit -m "WIP stripe"

# Commit 2: Add actual implementation
cat > stripe_gateway.py << 'EOF'
"""Stripe payment gateway implementation."""
import requests
from typing import Optional

class StripeGateway:
    BASE_URL = "https://api.stripe.com/v1"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.auth = (api_key, "")  # Stripe uses API key as username
    
    def charge(
        self,
        amount_cents: int,
        currency: str,
        customer_id: str,
        idempotency_key: Optional[str] = None
    ) -> dict:
        print(f"DEBUG: charging {amount_cents}")  # Leftover debug line
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        
        response = self.session.post(
            f"{self.BASE_URL}/charges",
            data={
                "amount": amount_cents,
                "currency": currency,
                "customer": customer_id,
            },
            headers=headers,
        )
        response.raise_for_status()
        return response.json()
EOF

git add stripe_gateway.py
git commit -m "add charge method"

# Commit 3: Forgot to update requirements
cat >> requirements.txt << 'EOF'
stripe>=5.0.0
EOF

git add requirements.txt
git commit -m "forgot requirements"

# Commit 4: Add error handling (this is logically part of the implementation)
cat > stripe_gateway.py << 'EOF'
"""Stripe payment gateway implementation."""
import requests
from typing import Optional

class StripeGatewayError(Exception):
    """Raised when Stripe API returns an error."""
    def __init__(self, message: str, code: str, decline_code: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.decline_code = decline_code

class StripeGateway:
    BASE_URL = "https://api.stripe.com/v1"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.auth = (api_key, "")
    
    def charge(
        self,
        amount_cents: int,
        currency: str,
        customer_id: str,
        idempotency_key: Optional[str] = None
    ) -> dict:
        print(f"DEBUG: charging {amount_cents}")  # Still here from debugging
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        
        response = self.session.post(
            f"{self.BASE_URL}/charges",
            data={
                "amount": amount_cents,
                "currency": currency,
                "customer": customer_id,
            },
            headers=headers,
        )
        
        if not response.ok:
            error_data = response.json().get("error", {})
            raise StripeGatewayError(
                message=error_data.get("message", "Unknown error"),
                code=error_data.get("code", "unknown"),
                decline_code=error_data.get("decline_code"),
            )
        
        return response.json()
EOF

git add stripe_gateway.py
git commit -m "Add error handilng"  # Note the typo

# Commit 5: Fix the debug line we should have removed earlier
sed -i.bak 's/print(f"DEBUG.*/#  Removed debug line/' stripe_gateway.py
rm stripe_gateway.py.bak 2>/dev/null || true
git add stripe_gateway.py
git commit -m "remove debug print"

# Commit 6: Actually remove it properly (we messed up the sed)
cat > stripe_gateway.py << 'EOF'
"""Stripe payment gateway implementation."""
import requests
from typing import Optional

class StripeGatewayError(Exception):
    """Raised when Stripe API returns an error."""
    def __init__(self, message: str, code: str, decline_code: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.decline_code = decline_code

class StripeGateway:
    BASE_URL = "https://api.stripe.com/v1"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.auth = (api_key, "")
    
    def charge(
        self,
        amount_cents: int,
        currency: str,
        customer_id: str,
        idempotency_key: Optional[str] = None
    ) -> dict:
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        
        response = self.session.post(
            f"{self.BASE_URL}/charges",
            data={
                "amount": amount_cents,
                "currency": currency,
                "customer": customer_id,
            },
            headers=headers,
        )
        
        if not response.ok:
            error_data = response.json().get("error", {})
            raise StripeGatewayError(
                message=error_data.get("message", "Unknown error"),
                code=error_data.get("code", "unknown"),
                decline_code=error_data.get("decline_code"),
            )
        
        return response.json()
EOF

git add stripe_gateway.py
git commit -m "actually remove debug"

# Commit 7: Add tests (separate logical unit)
mkdir -p tests
cat > tests/test_stripe_gateway.py << 'EOF'
"""Tests for Stripe gateway integration."""
import pytest
from unittest.mock import Mock, patch
from stripe_gateway import StripeGateway, StripeGatewayError

class TestStripeGateway:
    def test_charge_success(self):
        """Successful charge returns parsed response."""
        gateway = StripeGateway(api_key="sk_test_xxx")
        
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "id": "ch_1234567890",
            "amount": 5000,
            "currency": "usd",
            "status": "succeeded",
        }
        
        with patch.object(gateway.session, "post", return_value=mock_response):
            result = gateway.charge(
                amount_cents=5000,
                currency="usd",
                customer_id="cus_abc123",
            )
        
        assert result["id"] == "ch_1234567890"
        assert result["status"] == "succeeded"
    
    def test_charge_declined_raises_error(self):
        """Declined charge raises StripeGatewayError with decline code."""
        gateway = StripeGateway(api_key="sk_test_xxx")
        
        mock_response = Mock()
        mock_response.ok = False
        mock_response.json.return_value = {
            "error": {
                "message": "Your card was declined.",
                "code": "card_declined",
                "decline_code": "insufficient_funds",
            }
        }
        
        with patch.object(gateway.session, "post", return_value=mock_response):
            with pytest.raises(StripeGatewayError) as exc_info:
                gateway.charge(
                    amount_cents=5000,
                    currency="usd",
                    customer_id="cus_abc123",
                )
        
        assert exc_info.value.code == "card_declined"
        assert exc_info.value.decline_code == "insufficient_funds"
EOF

git add tests/
git commit -m "Add unit tests for stripe gateway"
```

### Step 3: Examine the Messy History

Before rebasing, let's see what we're working with:

```bash
git log --oneline main..HEAD
```

Output will show something like:

```
a1b2c3d Add unit tests for stripe gateway
d4e5f6g actually remove debug
h7i8j9k remove debug print
l0m1n2o Add error handilng
p3q4r5s forgot requirements
t6u7v8w add charge method
x9y0z1a WIP stripe
```

This history is problematic for reviewers:
- "WIP stripe" tells them nothing
- Multiple commits fixing the same debug line
- Typo in "handilng"
- "forgot requirements" should be squashed with the implementation

### Step 4: Plan the Rebase Strategy

Our goal is to create this clean history:

1. **feat(payments): add Stripe gateway with charge support** — main implementation + requirements
2. **feat(payments): add error handling for Stripe API failures** — error handling (with fixed typo)
3. **test(payments): add unit tests for Stripe gateway** — tests

We'll use interactive rebase to:
- **squash** the WIP commit, charge method, forgotten requirements, and debug removal into one
- **reword** the error handling commit to fix the typo
- **keep** the test commit as-is

### Step 5: Perform the Interactive Rebase

```bash
# Rebase all commits since branching from main
# The ^ means "parent of main" - we want to replay everything after main
git rebase -i main
```

Git opens your editor with a todo list. The **original file** looks like this:

```
pick x9y0z1a WIP stripe
pick t6u7v8w add charge method
pick p3q4r5s forgot requirements
pick l0m1n2o Add error handilng
pick h7i8j9k remove debug print
pick d4e5f6g actually remove debug
pick a1b2c3d Add unit tests for stripe gateway
```

**Edit it to become:**

```
# Combine all implementation commits into one clean commit
# 'reword' lets us write a proper commit message
reword x9y0z1a WIP stripe
squash t6u7v8w add charge method
squash p3q4r5s forgot requirements
squash h7i8j9k remove debug print
squash d4e5f6g actually remove debug

# Fix the typo in the error handling commit
reword l0m1n2o Add error handilng

# Tests are fine as-is
pick a1b2c3d Add unit tests for stripe gateway
```

**Key rebase commands explained:**
- `pick` — use this commit as-is
- `reword` — use this commit