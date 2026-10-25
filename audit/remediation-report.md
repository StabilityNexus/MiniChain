# MiniChain remediation report

This report covers the findings from the CLI audit of commit `f014f9f` (22 October 2026). I went through each finding, checked it against the code, fixed what was real, and tested the result. The fixes are in [PR #171](https://github.com/StabilityNexus/MiniChain/pull/171).

## Summary

The audit confirmed that the chain itself works: mining, transfers, saving and restoring state, two-node sync and contracts all behaved correctly. The findings were small ones in the CLI output and the docs. All seven are addressed.

## Findings and fixes

| # | Finding | Fix |
|---|---------|-----|
| 1 | The README deploy example (`deploy examples/counter.py 0 100`) gave the contract too little gas to deploy. | The example is now `deploy examples/counter.py 0 20000 0`. |
| 2 | The README and `help` showed `deploy` and `call` with `[amount] [fee]`. The CLI reads `[amount] [gas_limit] [fee_per_gas]`. | Both now show the real arguments and say a gas limit is needed. |
| 3 | After `mine`, a failed contract transaction was counted like any other mined transaction. | `mine` now prints one result line per transaction with the status, gas used, the error if there is one, and the contract address for deploys. |
| 4 | `balance` shortened every address, so contract addresses could not be copied into `call`. | `balance` prints full addresses. |
| 5 | A second `send` before mining gave a general rejection message. | The message now says a transaction with that nonce is probably pending, and to mine first or raise the fee. |
| 6 | `keystore.json` was created with the default file permissions. | It is created readable only by its owner, like `nodekey`. The README notes the file is unencrypted. |
| 7 | `help` did not list `ban`, `unban` and `list-banned`. | All three are listed. |

I also moved the default ports (9000 and 8545) and the key file mode into `node_config.py`, so they sit with the other settings.

## One more fix

While running the full test suite I found that the contract run-time limit of 5 seconds started when the sandbox process launched. On Windows, launching takes about 4 seconds, and a nested contract call launches another process, so cross-contract tests timed out. The limit now starts once the sandbox process reports that it is running, and time spent waiting on a nested call is not charged to the caller. A contract that loops forever is still stopped after 5 seconds of its own running time. Both timeouts are settings in `network_config.py`.

## How I tested

- I ran the full test suite on the PR branch: 245 passed.
- I ran the old README deploy command against the code. It now shows a red failed line with "Out of gas" and a gas use of 100.
- I ran the new README deploy command. It shows a green succeeded line with 15,220 gas used and the full 40 character contract address.
- I checked that `help` lists the three ban commands and the gas arguments.
- I checked that `balance` shows the full contract address.
