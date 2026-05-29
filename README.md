# Defensible AML Decision Workbench

A working prototype that demonstrates how AI-generated AML (anti money
laundering) case summaries can be deterministically verified against source
data before a human approves them.

## Build principle

Structured claims first. Deterministic verification second. Human approval last.

The AI does not get to write free text that a reviewer rubber-stamps. The AI
emits structured claims drawn from a closed vocabulary. Each claim is checked
against the underlying records by deterministic code. A human only approves
after seeing which claims passed, failed, or need review.

## Project status: Phase 1 (foundation)

This phase delivers the foundation only:

- Project scaffolding
- Data schema (9 tables, Pydantic v2)
- Synthetic dataset with planted "hero" demo cases
- Closed claim-type vocabulary (9 types)
- Stubs for the verifier and audit modules

Not in this phase: the Streamlit UI, real LLM integration, and real verifier
pattern-matching logic. Those arrive in later weeks.

## Layout

```
data/        synthetic CSVs, one per table (generated, reproducible)
src/         Python modules (schema, verifier, audit, pipeline)
docs/        specs and design docs (schema.md, claim_types.md)
scripts/     data generation
tests/       pytest sanity checks for the planted hero cases
```

## Quickstart

```bash
pip install -r requirements.txt
python scripts/generate_data.py   # (re)generate the synthetic CSVs
python -m pytest                  # run the data-integrity tests
```

Data generation uses a fixed random seed, so the CSVs are byte-for-byte
reproducible across runs.

## Hero cases

The synthetic data has planted demo moments. See `scripts/generate_data.py`
(search for "HERO CASE") and the "Hero cases" section of `docs/schema.md` for
exactly where each one lives and what it demonstrates.

## Module map

| Module           | Status in Phase 1 | Purpose                                          |
|------------------|-------------------|--------------------------------------------------|
| `src/schema.py`  | Complete          | Pydantic v2 models for all 9 tables              |
| `src/audit.py`   | Complete          | Append-only audit log writer                     |
| `src/verifier.py`| Stub              | Claim verification dispatcher (logic in wk 5-6)  |
| `src/pipeline.py`| Stub              | End-to-end orchestration outline                 |

## Documentation

- `docs/schema.md`: the 9 tables, field descriptions, and relationships.
- `docs/claim_types.md`: the closed claim-type vocabulary, written for
  non-engineers.
