# Difficulty Adjustment

MiniChain adjusts mining difficulty after every block with an exponential
moving average (EMA) of block times. There is no fixed retarget window and no
PID controller.

## Algorithm

For each accepted block:

```text
avg = alpha * (block.timestamp - previous.timestamp) + (1 - alpha) * avg
next_target = previous_target * avg / target_block_time
```

`target_block_time` and `alpha` come from `genesis.json` (`10000` ms and `0.1`
in the default configuration). A larger target is easier to mine.

## Bounds

`minichain/network_config.py` defines two consensus-critical limits:

- `MAX_TARGET_CHANGE_PERCENT = 25`: the target may move by at most 25% in one
  block. This prevents one unusually short or long timestamp interval from
  moving difficulty by orders of magnitude.
- `MIN_DIFFICULTY_FLOOR_DIVISOR = 4`: difficulty may not fall below one quarter
  of the genesis difficulty. In target terms, the easiest allowed target is
  `min(MAX_TARGET, genesis_target * 4)`.

## Restarts and Reorgs

`current_target` and `avg_block_time` are derived state. They are not part of a
block header and are not hashed. When a chain is loaded or a reorg switches to a
new branch, MiniChain replays the canonical timestamps to rebuild the EMA and
the next target. SQLite snapshots also persist `target_block_time` and `alpha`,
so a restart does not depend on whatever `genesis.json` happens to be in the
working directory.

Historical targets are not revalidated during this replay. Chains produced by
older retarget rules therefore still load and continue from their stored
targets.

## Follow-up

`target_block_time` and `alpha` affect consensus but are not committed in the
genesis hash. Nodes must use the same values and the same `network_config.py`
constants. Committing the full difficulty configuration to the genesis hash is
left for a future change.
