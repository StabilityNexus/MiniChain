# MiniChain End-to-End Testing

This guide covers full validation on one machine and across two nodes.

## 1) Setup

```bash
source .venv/bin/activate
python -m pip install -e .
```

Optional clean start:

```bash
rm -rf .demo-single .demo-a .demo-b
```

## 2) Shell Variable Rule

Use `VAR=value` (no `$` in assignment).

Correct:

```bash
MINER_ADDR=50e7432a0467eeb6ba39abea65a0ac491c8ca6b2
echo "$MINER_ADDR"
```

Wrong:

```bash
MINER_ADDR=$50e7432a0467eeb6ba39abea65a0ac491c8ca6b2
```

## 3) Single-Node Flow With Logs

Terminal A:

```bash
MINER_OUT=$(minichain --data-dir .demo-single wallet generate-key)
RECIP_OUT=$(minichain --data-dir .demo-single wallet generate-key)

MINER_ADDR=$(echo "$MINER_OUT" | awk -F= '/address/{gsub(/ /,"",$2); print $2; exit}')
MINER_PK=$(echo "$MINER_OUT" | awk -F= '/private_key/{gsub(/ /,"",$2); print $2; exit}')
RECIP_ADDR=$(echo "$RECIP_OUT" | awk -F= '/address/{gsub(/ /,"",$2); print $2; exit}')

minichain --data-dir .demo-single --host 127.0.0.1 --port 7000 --miner-address "$MINER_ADDR" \
  node run --advertise-host 127.0.0.1 --mine --mine-interval-seconds 10 --status-interval-seconds 2 \
  | tee node-single.log
```

Terminal B:

```bash
minichain --data-dir .demo-single chain info

minichain --data-dir .demo-single --host 127.0.0.1 --port 7000 tx submit \
  --private-key "$MINER_PK" \
  --recipient "$RECIP_ADDR" \
  --amount 5 \
  --fee 1 \
  --no-mine-now
```

After next mined block:

```bash
minichain --data-dir .demo-single wallet balance --address "$MINER_ADDR"
minichain --data-dir .demo-single wallet balance --address "$RECIP_ADDR"
minichain --data-dir .demo-single chain info
minichain --data-dir .demo-single chain accounts --limit 20
```

Proof logs:

```bash
grep -E "\\[  MINE  \\]|\\[ STATUS \\]|tx_accepted|block_extended" node-single.log | tail -n 50
```

Stop:

```bash
minichain --data-dir .demo-single node stop
```

## 4) Two Nodes on One Machine (Multi-Device Simulation)

Terminal A (miner):

```bash
MINER_OUT=$(minichain --data-dir .demo-a wallet generate-key)
MINER_ADDR=$(echo "$MINER_OUT" | awk -F= '/address/{gsub(/ /,"",$2); print $2; exit}')
MINER_PK=$(echo "$MINER_OUT" | awk -F= '/private_key/{gsub(/ /,"",$2); print $2; exit}')

minichain --data-dir .demo-a --host 127.0.0.1 --port 7000 --miner-address "$MINER_ADDR" \
  node run --advertise-host 127.0.0.1 --mine --mine-interval-seconds 15 --status-interval-seconds 2 \
  | tee nodeA.log
```

Terminal B (observer):

```bash
RECIP_OUT=$(minichain --data-dir .demo-b wallet generate-key)
RECIP_ADDR=$(echo "$RECIP_OUT" | awk -F= '/address/{gsub(/ /,"",$2); print $2; exit}')

minichain --data-dir .demo-b --host 127.0.0.1 --port 7001 \
  node run --advertise-host 127.0.0.1 --peer 127.0.0.1:7000 --status-interval-seconds 2 \
  | tee nodeB.log
```

Terminal C (checks + tx):

```bash
grep "connected_peers=" nodeA.log | tail -n 5
grep "connected_peers=" nodeB.log | tail -n 5

minichain --data-dir .demo-a --host 127.0.0.1 --port 7000 tx submit \
  --private-key "$MINER_PK" \
  --recipient "$RECIP_ADDR" \
  --amount 7 \
  --fee 1 \
  --no-mine-now
```

After next mined block:

```bash
minichain --data-dir .demo-a chain info
minichain --data-dir .demo-b chain info
minichain --data-dir .demo-a wallet balance --address "$MINER_ADDR"
minichain --data-dir .demo-a wallet balance --address "$RECIP_ADDR"
minichain --data-dir .demo-b wallet balance --address "$MINER_ADDR"
minichain --data-dir .demo-b wallet balance --address "$RECIP_ADDR"
```

Stop:

```bash
minichain --data-dir .demo-a node stop
minichain --data-dir .demo-b node stop
```

## 5) True Multi-Device LAN

- Device A starts miner daemon and advertises `<A_IP>`.
- Device B starts observer daemon and peers to `<A_IP>:7000`.
- Use the same `chain info`, `wallet balance`, and `tx submit` checks as section 4.

Device A:

```bash
minichain --data-dir .demo-a --host 0.0.0.0 --port 7000 --miner-address "$MINER_ADDR" \
  node run --advertise-host <A_IP> --mine --mine-interval-seconds 15 --status-interval-seconds 2
```

Device B:

```bash
minichain --data-dir .demo-b --host 0.0.0.0 --port 7001 \
  node run --advertise-host <B_IP> --peer <A_IP>:7000 --status-interval-seconds 2
```

## 6) What To Verify

- `chain info` matches across peers (`height`, `tip_hash`, `connected_peers`).
- Receiver balance changes after mined inclusion.
- Logs show mining and network events.
