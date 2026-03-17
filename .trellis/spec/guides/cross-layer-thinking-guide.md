# Cross-Layer Thinking Guide

> **Purpose**: Think through data flow across layers before implementing.

---

## The Problem

**Most bugs happen at layer boundaries**, not within layers.

In this repository the risky boundaries are usually:

- benchmark JSON -> `Problem` dataclass -> pipeline/webapp selection
- model discovery metadata -> generation request payloads -> persisted API traces
- evaluator artifacts -> raw run JSON -> leaderboard rebuild -> web UI rendering
- web UI config -> environment injection -> provider calls
- per-attempt case records -> final-case selection -> suite summary and slice rankings

---

## Before Implementing Cross-Layer Features

### Step 1: Map the Data Flow

Draw out how data moves:

```
Benchmark JSON / UI Request
  -> normalize to dataclasses / config dicts
  -> execute provider or tooling work
  -> persist raw run state and artifacts
  -> rebuild scored summaries
  -> serve CLI or web payloads
```

For each arrow, ask:
- What format is the data in?
- What could go wrong?
- Who is responsible for validation?

### Step 2: Identify Boundaries

| Boundary | Common Issues |
|----------|---------------|
| `problem_bank.py` ↔ pipeline/webapp | Missing inferred metadata, invalid task payloads |
| `model_sources.py` ↔ `model_runner.py` | Provider mismatch, missing auth metadata, model id normalization |
| `model_runner.py` ↔ `pipeline.py`/`webapp.py` | Empty generation output, trace persistence, retry feedback |
| `evaluator.py` ↔ leaderboard/webapp | Missing artifact paths, inconsistent stage statuses |
| raw run JSON ↔ `leaderboard.py` | Schema drift, old runs missing new fields |
| `webapp.py` API ↔ `webui/app.js` | Response shape mismatches, missing derived fields |

### Step 3: Define Contracts

For each boundary:
- What is the exact input format?
- What is the exact output format?
- What errors can occur?
- Which fields are source-of-truth versus derived?

---

## Common Cross-Layer Mistakes

### Mistake 1: Adding a field in only one place

**Bad**: Writing `problem_track` or `api_trace` in one run path but not the other

**Good**: Update both `pipeline.py` and `webapp.py` when they emit the same family of case records

### Mistake 2: Assuming leaderboard state is primary data

**Bad**: Changing `results/leaderboard.json` logic without checking raw-run rebuild behavior

**Good**: Treat `results/raw/*.json` plus rebuild code as the durable contract

### Mistake 3: Breaking artifact discoverability

**Bad**: Renaming evaluator outputs or directory layout without updating `list_case_artifacts()` and web artifact loading

**Good**: Keep artifact naming stable or update every reader in the same patch

---

## Checklist for Cross-Layer Features

Before implementation:
- [ ] Mapped the complete data flow
- [ ] Identified all layer boundaries
- [ ] Defined format at each boundary
- [ ] Decided where validation happens
- [ ] Checked whether `pipeline.py` and `webapp.py` both need the change
- [ ] Checked whether old raw runs can still be read

After implementation:
- [ ] Tested with edge cases (null, empty, invalid)
- [ ] Verified error handling at each boundary
- [ ] Checked data survives round-trip
- [ ] Confirmed artifact paths still resolve
- [ ] Confirmed leaderboard rebuild still works from raw results

---

## When to Create Flow Documentation

Create detailed flow docs when:
- Feature spans 3+ layers
- Multiple teams are involved
- Data format is complex
- Feature has caused bugs before
- A new benchmark metadata field must appear in CLI, leaderboard, and web UI outputs
