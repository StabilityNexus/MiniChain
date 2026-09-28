# Remediations of Security Review Findings

Review date and time: 2026-09-27T21:23:44Z

## Remediations

### Remediation of Finding 1: Sandbox escape via `str.format()` attribute-chain traversal bypasses dunder blocklist

- [x] This remediation implements the security review's recommendation for this finding.
- [ ] This remediation addresses the finding in a way that differs from the security review's recommendation.

Remediation commits:
- [3ca9596](https://github.com/StabilityNexus/MiniChain/commit/3ca9596ee4077b5015e263c458bb469ed082f384)

Description: `_validate_code_ast` in `minichain/contract.py` now rejects any AST `Attribute` access to `.format()` or `.format_map()` outright. Without that method, contract code has no way to reach `str.format()`'s field-name mini-language, which was the actual mechanism letting a runtime-assembled dunder string (built via concatenation, so no literal ever contained `"__"`) traverse into `__globals__` and beyond. A regression test (`test_malicious_format_string_sandbox_escape` in `tests/test_contract.py`) deploys and calls a contract using the exact exploit payload end-to-end through `State.apply_transaction`, asserting it now fails AST validation.

## Non-remediated findings

None.

## Comments

None.
