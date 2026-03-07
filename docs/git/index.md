---
title: git - Technical Overview
tags: [git, data-engineering, devtools]
---

# git — Technical Overview

## What It Is

Git is a distributed version control system that tracks content changes as a directed acyclic graph (DAG) of snapshots, not diffs. It operates locally-first with optional remote synchronization, making every clone a full repository with complete history. Git is NOT a backup system, NOT a deployment tool, and NOT a substitute for actual release management—it's a content-addressable filesystem with a VCS user interface bolted on top. It solves the problem of tracking, merging, and distributing code changes across distributed teams without requiring constant network connectivity or a central authority.

## Core Concepts

**Content-Addressable Storage** — Every object (blob, tree, commit, tag) is identified by its SHA-1 hash. Git doesn't store diffs; it stores complete snapshots and computes differences on demand. Understanding this explains why operations like `checkout` are fast and why history rewriting changes commit hashes downstream.

**Three Trees** — The working directory (your filesystem), the index/staging area (proposed next commit), and HEAD (last commit on current branch). Every git operation manipulates one or more of these trees. Confusing these is the source of most beginner and intermediate mistakes.

**Refs and the Reflog** — Branches and tags are just pointers (refs) to commits. The reflog tracks where refs pointed historically, which is your safety net when you think you've lost work. Refs are mutable; commits are immutable.

**Merge vs. Rebase Semantics** — Merge creates a new commit with two parents preserving branch topology. Rebase replays commits onto a new base, rewriting history for a linear graph. Neither is universally better; the choice depends on whether you value history accuracy or history cleanliness.

**Remote Tracking Branches** — `origin/main` is not `main`. Remote tracking branches are local copies of where remote refs pointed at last fetch. `git pull` is `git fetch` + `git merge`; understanding this prevents confusion about diverged histories.

**The Object Model** — Commits point to trees, trees point to blobs and other trees. This forms the DAG. A commit contains metadata (author, committer, message, parent refs) plus a pointer to the root tree. Everything else is derived from traversing this graph.

## Primary Use Cases

### Collaborative Feature Development
- **When to reach for it:** Multiple engineers working on the same codebase, need to isolate changes, review before merge, maintain audit trail
- **When NOT to reach for it:** Single-developer throwaway scripts, content that changes rarely (use artifact storage), binary assets that don't diff well (use Git LFS or dedicated asset management)

### Code Review Workflow (Pull/Merge Requests)
- **When to reach for it:** Team requires approval gates, CI must pass before merge, need discussion threads tied to specific changes
- **When NOT to reach for it:** Hotfixes requiring immediate deployment (have a bypass process), trivial changes where ceremony exceeds value

### Release Management and Tagging
- **When to reach for it:** Need immutable markers for shipped versions, must correlate deployments to exact code state, audit requirements
- **When NOT to reach for it:** Continuous deployment where every commit to main deploys (tags become noise), environments managed by commit SHA directly

### History Archaeology and Debugging
- **When to reach for it:** `git bisect` to find regression-introducing commits, `git blame` to understand why code exists, `git log -S` to find when code was added/removed
- **When NOT to reach for it:** Debugging runtime behavior (use APM/tracing), understanding system behavior (use observability tooling)

### Configuration and Infrastructure as Code
- **When to reach for it:** Terraform, Kubernetes manifests, CI configs—anything declarative that benefits from change tracking and review
- **When NOT to reach for it:** Secrets (use vault/secrets manager), large generated files, environment-specific values that should be injected at runtime

## Senior / Staff Engineer Highlights

### Production Gotchas & Failure Modes

**Force Push to Shared Branches** — `git push --force` on a branch others have based work on creates divergent histories. Their next pull fails or silently creates merge commits. Use `--force-with-lease` which fails if remote has commits you haven't seen. Better: never force-push shared branches; use revert commits instead.

**Large Files and Repository Bloat** — Git stores full snapshots. Add a 100MB file, delete it next commit, repo is still 100MB+ forever. `git filter-repo` or BFG can rewrite history but requires coordinated team effort. Prevention: `.gitattributes` with Git LFS for binaries, pre-commit hooks checking file sizes.

**Merge Commits in CI Creating Non-Reproducible Builds** — GitHub/GitLab can create merge commits for PRs that differ from local merges due to timing. Your CI tests the merge commit, but developers test their branch. Subtle test flakiness or "works on my machine" issues follow. Solution: require rebasing before merge or use merge queues.

**Submodule Hell** — Submodules pin to specific commits but don't auto-update. `git clone` doesn't fetch submodules by default. Developers work on stale dependencies unknowingly. CI caches submodules incorrectly. Alternative: monorepos, git subtree, or proper dependency management (npm, pip, go modules).

**Credential Leakage in History** — Committed secrets remain in history even after removal in subsequent commits. Automated scanners find them. Requires history rewriting which breaks all forks/clones. Prevention: pre-commit hooks (detect-secrets, trufflehog), separate secret management from day one.

**Shallow Clone Breakage** — CI systems often shallow clone (`--depth 1`) for speed. Operations requiring history (`git describe`, `git log`, merge-base detection) fail or give wrong answers. Some tooling silently falls back to wrong behavior. Know what your pipeline actually needs.

**Reflog Expiration** — The reflog that saves your "lost" commits expires (default 90 days for reachable, 30 for unreachable). Long-running branches with force-pushes can genuinely lose history. In shared repos, reflog is per-clone—your teammate's reflog won't save you.

### When NOT To Use git

**Large Binary Assets** — Git LFS helps but adds complexity. For multi-GB video/image assets, use dedicated artifact storage (S3, Artifactory, cloud storage) with references in git. Git's diffing and merge tooling provides zero value for binaries.

**Database Schema as Source of Truth** — Track migrations in git, but the database itself is the schema authority. Tools like Liquibase or Flyway bridge this, but don't expect git alone to manage schema state.

**Secrets and Credentials** — Use HashiCorp Vault, AWS Secrets Manager, or similar. Git-crypt and SOPS exist but add operational complexity. The moment a secret touches git history, assume it's compromised.

**Ephemeral/Generated Content** — Build artifacts, compiled code, node_modules, vendor directories. Generates massive repos, merge conflicts on generated content, and lies about what code produces what output.

**Real-time Collaboration** — Git's model is batch-oriented (commit, push). For Google Docs-style real-time collaboration, use CRDTs (Yjs, Automerge) or operational transform systems.

### How It Fits Into a Broader Stack

```
Developer Workstation
    └── git (local)
           │
           ▼
Remote Repository (GitHub/GitLab/Bitbucket)
    ├── PR/MR → Code Review
    ├── Webhooks → CI/CD (Actions, Jenkins, CircleCI)
    │                └── Build/Test/Deploy Pipelines
    ├── Branch Protection → Merge Requirements
    └── Release Tags → Artifact Registries (Docker, npm, PyPI)
                              │
                              ▼
                       Deployment Targets
                       (K8s, Lambda, VMs)
```

**Upstream:** IDEs (VSCode, JetBrains), pre-commit frameworks, local linters/formatters, development containers

**Downstream:** CI/CD systems, artifact registries, deployment orchestration, GitOps controllers (ArgoCD, Flux), infrastructure provisioning (Terraform, Pulumi)

**Patterns:**
- **GitOps:** Git as single source of truth for declarative infrastructure; controllers reconcile cluster state to repo state
- **Trunk-Based Development:** Short-lived branches, frequent merges to main, feature flags over long-lived branches
- **Git-Flow:** Long-lived develop/release branches; adds ceremony but provides release isolation (less common in continuous deployment)

### Performance & Scale Considerations

**Repository Size** — Git performance degrades noticeably past 1-2GB, painfully past 5GB. `git gc` helps temporarily. Solutions: shallow clones for CI, split into multiple repos, use partial clone (`--filter=blob:none`), VFS for Git (Microsoft's solution for Windows scale).

**File Count** — Repositories with >100k files see slow `git status` due to filesystem operations. `core.fsmonitor` with Watchman helps significantly. Sparse checkout reduces working directory size.

**History Depth** — Repositories with >100k commits slow down log operations and merges. Rarely a real problem unless combined with size issues, but `git replace` can graft shallow history.

**Monorepo Scaling** — Google/Meta scale requires custom tooling (Piper, Sapling). For most teams, path-based CODEOWNERS, sparse checkout, and build system caching (Bazel, Nx, Turborepo) make git monorepos tractable to ~100 engineers.

**Clone Time** — First clone of large repos is painful. Partial clone, shallow clone, or repo mirrors (geographic distribution) are the levers. CI should cache `.git` directories between runs.

**Detection:** Watch `git status` time, `git fetch` time, `.git` directory size, clone time for new team members. These are leading indicators before productivity collapses.

## Key Tradeoffs

| Aspect | Tradeoff |
|--------|----------|
| **Distributed vs. Centralized** | Every clone is complete (resilience, offline work) but means no single source of truth without convention; divergent histories are a feature, not a bug |
| **Immutable History vs. Rewriting** | Clean history aids understanding but rewriting shared history breaks collaborators; pick a team convention and enforce it |
| **Flexibility vs. Guardrails** | Git lets you do almost anything (freedom) but won't stop you from destroying work (footgun); requires tooling layer (branch protection, hooks) for safety |
| **Snapshot Storage vs. Diff Storage** | Fast checkouts and comparisons but poor storage efficiency for large binaries; designed for text, retrofit solutions (LFS) add complexity |
| **Local-First vs. Network-First** | Excellent offline experience and speed but sync conflicts must be resolved manually; no automatic conflict resolution |

## Quick Reference

```bash
# Daily workflow
git status                          # What's changed
git diff                            # Unstaged changes
git diff --staged                   # Staged changes
git add -p                          # Interactive staging (partial files)
git commit -m "msg"                 # Commit staged changes
git push origin HEAD                # Push current branch

# Branch management
git switch -c feature/foo           # Create and switch (prefer over checkout)
git switch main                     # Switch branches
git branch -d feature/foo           # Delete merged branch
git branch -D feature/foo           # Force delete unmerged

# Sync and merge
git fetch --all --prune             # Fetch all remotes, clean dead refs
git pull --rebase                   # Rebase local commits on top of upstream
git rebase -i HEAD~3                # Interactive rebase last 3 commits
git merge --no-ff feature/foo       # Merge with explicit merge commit

# History investigation
git log --oneline --graph -20       # Visual recent history
git log -S "search_term"            # Find commits adding/removing text
git blame -L 10,20 file.py          # Who changed lines 10-20
git bisect start/good/bad           # Binary search for bug introduction

# Recovery and undo
git reflog                          # See where HEAD has been
git reset --hard HEAD~1             # Undo last commit (destructive)
git reset --soft HEAD~1             # Undo commit, keep changes staged
git restore file.py                 # Discard working directory changes
git restore --staged file.py        # Unstage file
git revert <sha>                    # Create commit undoing <sha>
git stash push -m "wip"             # Stash changes
git stash pop                       # Restore stashed changes

# Inspection
git show <sha>                      # Show commit details
git log --oneline main..HEAD        # Commits in current branch not in main
git cherry -v main                  # What would be cherry-picked

# Maintenance
git gc --aggressive                 # Garbage collect, compress
git fsck                            # Verify object database integrity
```