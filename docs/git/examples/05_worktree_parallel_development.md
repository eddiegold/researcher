---
title: Parallel Development with Git Worktrees
---

# Parallel Development with Git Worktrees

## Overview

Git worktrees allow you to check out multiple branches of the same repository into separate directories simultaneously, each with its own working tree but sharing a single `.git` database. This eliminates the context-switching cost of stashing work, switching branches, and restoring state—a workflow that becomes increasingly painful as projects grow in complexity.

This walkthrough demonstrates a realistic scenario: you're midway through a feature branch adding a new payment processing module when a critical production bug lands in your lap, and a colleague asks you to review their authentication refactor. Rather than juggling stashes or maintaining multiple clones, worktrees let you handle all three contexts in parallel with zero interference.

**Why this matters in real systems:**
- Build artifacts, IDE indexes, and node_modules remain intact per-branch
- No risk of stash conflicts or forgotten work-in-progress
- CI/CD scripts can build multiple branches simultaneously from the same repo
- Code reviews don't require abandoning your current debugging session

## Prerequisites

**Required:**
- Git 2.15+ (worktree improvements stabilized here; check with `git --version`)
- Basic familiarity with Git branching and remotes
- ~500MB disk space for the example (worktrees share object storage, but each needs a working copy)

**Assumed knowledge:**
- Comfort with terminal/shell operations
- Understanding of Git's branch/commit model
- Familiarity with typical feature branch workflows

**Optional but helpful:**
- A code editor that handles multiple project roots (VS Code, JetBrains IDEs)
- Understanding of Git's internal object model

## Implementation

### Step 1: Initialize the Primary Repository

First, let's create a realistic repository structure simulating a payment processing service. In production, this would be your existing clone—we're creating it fresh for demonstration purposes.

```bash
# Create the main project directory
mkdir -p ~/projects/payment-gateway
cd ~/projects/payment-gateway

# Initialize with a main branch (modern Git default)
git init --initial-branch=main

# Create a realistic project structure
mkdir -p src/{api,services,models} tests config
```

Now populate it with representative code that we'll modify across branches:

```python
# src/services/payment_processor.py
"""
Core payment processing service.
Handles transaction validation, gateway communication, and receipt generation.
"""
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class PaymentResult:
    transaction_id: str
    status: str  # 'completed', 'pending', 'failed'
    amount: Decimal
    currency: str
    processed_at: datetime
    gateway_reference: Optional[str] = None

class PaymentProcessor:
    SUPPORTED_CURRENCIES = {'USD', 'EUR', 'GBP', 'CAD'}
    MAX_TRANSACTION_AMOUNT = Decimal('50000.00')
    
    def __init__(self, gateway_client, config: dict):
        self._gateway = gateway_client
        self._merchant_id = config['merchant_id']
        self._environment = config.get('environment', 'sandbox')
    
    def process_payment(
        self,
        amount: Decimal,
        currency: str,
        card_token: str,  # Tokenized card data, never raw PAN
        idempotency_key: str
    ) -> PaymentResult:
        """
        Process a payment through the configured gateway.
        
        The idempotency_key prevents duplicate charges on retry—critical for
        production systems where network failures are common.
        """
        self._validate_payment_request(amount, currency)
        
        # Gateway interaction would happen here
        response = self._gateway.charge(
            merchant_id=self._merchant_id,
            amount=int(amount * 100),  # Gateway expects cents
            currency=currency,
            source=card_token,
            idempotency_key=idempotency_key
        )
        
        return PaymentResult(
            transaction_id=response['id'],
            status=self._map_gateway_status(response['status']),
            amount=amount,
            currency=currency,
            processed_at=datetime.utcnow(),
            gateway_reference=response.get('gateway_ref')
        )
    
    def _validate_payment_request(self, amount: Decimal, currency: str) -> None:
        if currency not in self.SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency: {currency}")
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if amount > self.MAX_TRANSACTION_AMOUNT:
            raise ValueError(f"Amount exceeds maximum: {self.MAX_TRANSACTION_AMOUNT}")
    
    def _map_gateway_status(self, gateway_status: str) -> str:
        """Map gateway-specific status codes to our internal status."""
        mapping = {
            'succeeded': 'completed',
            'processing': 'pending',
            'requires_action': 'pending',
            'failed': 'failed',
            'canceled': 'failed'
        }
        return mapping.get(gateway_status, 'pending')
```

```python
# src/api/payment_routes.py
"""
REST API endpoints for payment operations.
"""
from decimal import Decimal, InvalidOperation
import uuid

# In production, this would be Flask/FastAPI/Django
class PaymentAPI:
    def __init__(self, payment_processor):
        self._processor = payment_processor
    
    def create_payment(self, request_data: dict) -> dict:
        """
        POST /api/v1/payments
        
        Expected payload:
        {
            "amount": "99.99",
            "currency": "USD",
            "card_token": "tok_visa_4242",
            "client_reference": "order-12345"
        }
        """
        try:
            amount = Decimal(request_data['amount'])
        except (KeyError, InvalidOperation) as e:
            return {'error': 'Invalid amount', 'code': 'INVALID_AMOUNT'}, 400
        
        # Generate idempotency key from client reference to prevent duplicates
        idempotency_key = f"{request_data.get('client_reference', uuid.uuid4())}"
        
        result = self._processor.process_payment(
            amount=amount,
            currency=request_data['currency'],
            card_token=request_data['card_token'],
            idempotency_key=idempotency_key
        )
        
        return {
            'transaction_id': result.transaction_id,
            'status': result.status,
            'amount': str(result.amount),
            'currency': result.currency
        }, 201
```

Commit this as our baseline:

```bash
git add .
git commit -m "feat: Initial payment processing service

- PaymentProcessor with gateway abstraction
- REST API routes for payment creation
- Support for USD, EUR, GBP, CAD currencies
- Idempotency key support for safe retries"
```

### Step 2: Simulate Work-in-Progress on a Feature Branch

Before the hotfix arrives, let's start work on a new feature: adding refund capabilities. This represents your in-progress work that you don't want to stash or lose context on.

```bash
# Create and switch to the feature branch
git checkout -b feature/refund-processing

# Start implementing the refund feature
```

Add partial implementation (intentionally incomplete—this is work-in-progress):

```python
# src/services/refund_processor.py
"""
Refund processing service - WORK IN PROGRESS

TODO:
- [ ] Add partial refund support
- [ ] Implement refund reason categorization
- [ ] Add webhook notification on refund completion
"""
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class RefundResult:
    refund_id: str
    original_transaction_id: str
    amount: Decimal
    status: str
    processed_at: datetime

class RefundProcessor:
    """
    Handles refund operations against completed payments.
    
    Note: Refunds can only be issued against 'completed' transactions
    and must not exceed the original transaction amount.
    """
    
    def __init__(self, gateway_client, transaction_repository):
        self._gateway = gateway_client
        self._transactions = transaction_repository
    
    def process_refund(
        self,
        transaction_id: str,
        amount: Optional[Decimal] = None,  # None = full refund
        reason: str = "customer_request"
    ) -> RefundResult:
        # Fetch original transaction to validate refund
        original = self._transactions.get(transaction_id)
        if not original:
            raise ValueError(f"Transaction not found: {transaction_id}")
        
        if original.status != 'completed':
            raise ValueError(
                f"Cannot refund transaction in status: {original.status}"
            )
        
        refund_amount = amount or original.amount
        
        # TODO: Check cumulative refunds don't exceed original amount
        # This is where I stopped when the hotfix came in...
```

```bash
git add .
git commit -m "wip: Refund processor - partial implementation

Basic structure in place. Still need:
- Partial refund cumulative tracking
- Reason code enum
- Webhook integration"
```

At this point, you're mid-implementation with unsaved editor state, mental context about what you were doing, and possibly a running test watcher. A traditional workflow would require stashing everything.

### Step 3: Create Worktrees for Parallel Contexts

Now the critical production bug arrives: currency conversion is failing for EUR transactions. Let's set up worktrees to handle this without disrupting your feature work.

```bash
# Return to main branch for worktree creation (best practice)
git checkout main

# Create a directory structure for worktrees
# I recommend keeping worktrees in a sibling directory to the main repo
mkdir -p ~/projects/payment-gateway-worktrees
```

Create the hotfix worktree:

```bash
# Create a worktree for the hotfix, branching from main
# Syntax: git worktree add <path> -b <new-branch> <start-point>
git worktree add \
    ~/projects/payment-gateway-worktrees/hotfix-eur-currency \
    -b hotfix/eur-currency-conversion \
    main

# The -b flag creates a new branch; omit it to check out an existing branch
```

This command does three things:
1. Creates the directory `~/projects/payment-gateway-worktrees/hotfix-eur-currency`
2. Creates a new branch `hotfix/eur-currency-conversion` from `main`
3. Checks out that branch into the new directory

Create a worktree for the code review (checking out an existing branch):

```bash
# First, fetch your colleague's branch (simulating a remote branch)
# In reality, this would be: git fetch origin feature/auth-refactor
git branch feature/auth-refactor main  # Simulating the remote branch exists

# Create worktree for reviewing their branch
git worktree add \
    ~/projects/payment-gateway-worktrees/review-auth-refactor \
    feature/auth-refactor

# No -b flag because we're checking out an existing branch
```

Verify your worktree setup:

```bash
git worktree list
# Output:
# /home/user/projects/payment-gateway                              a1b2c3d [main]
# /home/user/projects/payment-gateway-worktrees/hotfix-eur-currency d4e5f6g [hotfix/eur-currency-conversion]
# /home/user/projects/payment-gateway-worktrees/review-auth-refactor a1b2c3d [feature/auth-refactor]
```

### Step 4: Work on the Hotfix in Isolation

Navigate to the hotfix worktree and implement the fix:

```bash
cd ~/projects/payment-gateway-worktrees/hotfix-eur-currency

# Verify you're on the right branch
git branch --show-current
# Output: hotfix/eur-currency-conversion

# Your editor can open this as a completely separate project
# code .  # or your preferred editor
```

Implement the fix:

```python
# src/services/payment_processor.py
# Add the bugfix - EUR currency was being rejected due to case sensitivity issue
# in gateway communication

class PaymentProcessor:
    SUPPORTED_CURRENCIES = {'USD', 'EUR', 'GBP', 'CAD'}
    MAX_TRANSACTION_AMOUNT = Decimal('50000.00')
    
    # Currency codes that the gateway expects in lowercase
    # (discovered this during production incident debugging)
    _GATEWAY_LOWERCASE_CURRENCIES = {'EUR', 'GBP'}
    
    def __init__(self, gateway_client, config: dict):
        self._gateway = gateway_client
        self._merchant_id = config['merchant_id']
        self._environment = config.get('environment', 'sandbox')
    
    def process_payment(
        self,
        amount: Decimal,
        currency: str,
        card_token: str,
        idempotency_key: str
    ) -> PaymentResult:
        self._validate_payment_request(amount, currency)
        
        # FIX: Gateway API requires lowercase currency codes for EUR/GBP
        # but uppercase for USD/CAD (legacy API inconsistency)
        gateway_currency = self._normalize_currency_for_gateway(currency)
        
        response = self._gateway.charge(
            merchant_id=self._merchant_id,
            amount=int(amount * 100),
            currency=gateway_currency,  # Use normalized currency
            source=card_token,
            idempotency_key=idempotency_key
        )
        
        return PaymentResult(
            transaction_id=response['id'],
            status=self._map_gateway_status(response['status']),
            amount=amount,
            currency=currency,  # Return original format to caller
            processed_at=datetime.utcnow(),
            gateway_reference=response.get('gateway_ref')
        )
    
    def _normalize_currency_for_gateway(self, currency: str