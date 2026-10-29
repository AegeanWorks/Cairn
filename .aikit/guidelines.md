# Project Guidelines

Rules for this project only, written by people or AI agents. They add to AIKit's principles and
rank below them: a guideline never overrides a principle (PRINCIPLE-000 R6, PRINCIPLE-001 R4).
Add one with `python aikit.py guideline <name> "<rule>" --source <user|ADR-NNN|person>`, or by
hand as one line below in the form `- **G-NNN** (source): rule`; IDs are never reused.

Persisted state carries a format version. A loader refuses a version it does not support,
without modifying or quarantining that file; older versions migrate when a migration exists.

## Rules
- **G-001** (ADR-003): a new diagnostic check takes a new code from its stage family and is listed in README.md in the same change.
- **G-002** (ADR-002): a new machine instruction enters machine.py as one documented method with a test, never as an inline byte string elsewhere.
- **G-003** (ADR-009): a core value or its evidence command changes only with a new ADR, and every value keeps a live guard test named in values.py
