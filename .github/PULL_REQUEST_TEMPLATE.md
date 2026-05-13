# Pull Request

## Summary

Briefly describe what this PR does and why.

## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Documentation update
- [ ] Knowledge bank entry / domain pack contribution
- [ ] New analytical method
- [ ] New Decision Contracts action type
- [ ] MCP tool addition
- [ ] Breaking change (would cause existing functionality to change)

## What changed

Concrete list of files / behaviours touched.

- 
- 
- 

## Glass-box compliance

Confirm the principles in [ARCHITECTURE.md](../ARCHITECTURE.md) are upheld:

- [ ] Local-first — no required cloud dependencies introduced
- [ ] Glass-box — no silent failures, no hidden state, no undocumented heuristics
- [ ] Anti-hallucination — the LLM does not make analytical decisions
- [ ] Deterministic — same input + seed produces same output (Aurora Bundle content hash stable)
- [ ] Honest reporting — sampling / timeouts / skips are surfaced to the user
- [ ] Substrate-shaped — public API surfaces (SDK / MCP / Contracts) remain backward-compatible

If a checkbox can't be checked, please explain in the comments.

## Tests

- [ ] Added new tests for new code
- [ ] All existing tests still pass (`pytest`)
- [ ] Tests cover error / edge cases, not just the happy path

Test count before this PR: \_\_\_\_
Test count after this PR: \_\_\_\_

## Documentation

- [ ] Updated relevant docs (`docs/`, README, ARCHITECTURE, CHANGELOG)
- [ ] Added docstrings to new public APIs
- [ ] Updated method registry / citations in `research_kit.py` (if a new method)

## Related issues

Closes #\_\_\_\_
Related to #\_\_\_\_

## Reviewer checklist

(for maintainers)

- [ ] Code style: `black` + `ruff` clean
- [ ] No regressions in the full test sweep
- [ ] Frontend changes (if any) pass `tests/test_v5_frontend_audit.py`
- [ ] CHANGELOG.md updated
- [ ] If adding a method or domain: knowledge bank entry filed (or issue opened for follow-up)
