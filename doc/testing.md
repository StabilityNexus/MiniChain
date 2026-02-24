# MiniChain End-to-End Testing

This document is the single source for testing MiniChain end-to-end:
- single-device flow with live logs
- two-node sync on one machine (multi-device simulation)
- true multi-device LAN sync

## 1) Prerequisites

```bash
source .venv/bin/activate
python -m pip install -e .
```

Optional clean start:

```bash
rm -rf .demo-single .demo-a .demo-b
```


## 3) Single-Device End-to-End 

Use two terminals.

### Terminal A: create keys and run node daemon

```bash
MINER_OUT=$(minichain --data-dir .demo-single wallet generate-key)
RECIP_OUT=$(minichain --data-dir .demo-single wallet generate-key)

MINER_ADDR=$(echo "$MINER_OUT" | awk -F= '/address/{gsub(/ /,"",$2); print $2; exit}')
MINER_PK=$(echo "$MINER_OUT" | awk -F= '/private_key/{gsub(/ /,"",$2); print $2; exit}')
RECIP_ADDR=$(echo "$RECIP_OUT" | awk -F= '/address/{gsub(/ /,"",$2); print $2; exit}')

minichain --data-dir .demo-single --host 127.0.0.1 --port 7000 --miner-address "$MINER_ADDR" \
  node run --advertise-host 127.0.0.1 --mine --mine-interval-seconds 10 --status-interval-seconds 2 
```

### Terminal B: verify chain, submit tx, verify balances


```bash
minichain --data-dir .demo-single chain info

minichain --data-dir .demo-single --host 127.0.0.1 --port 7000 tx submit \
  --private-key "$MINER_PK" \
  --recipient "$RECIP_ADDR" \
  --amount 5 \
  --fee 1 \
  --no-mine-now
```

Wait for the next mined block, then:

```bash
minichain --data-dir .demo-single wallet balance --address "$MINER_ADDR"
minichain --data-dir .demo-single wallet balance --address "$RECIP_ADDR"
minichain --data-dir .demo-single chain accounts --limit 20
```

Stop daemon:

```bash
minichain --data-dir .demo-single node stop
```

## 4) Two Nodes on One Machine (Simulate Multi-Device)

Use two terminals.

### Terminal A (miner node)

```bash
MINER_OUT=$(minichain --data-dir .demo-a wallet generate-key)
MINER_ADDR=$(echo "$MINER_OUT" | awk -F= '/address/{gsub(/ /,"",$2); print $2; exit}')
MINER_PK=$(echo "$MINER_OUT" | awk -F= '/private_key/{gsub(/ /,"",$2); print $2; exit}')

minichain --data-dir .demo-a --host 127.0.0.1 --port 7000 --miner-address "$MINER_ADDR" \
  node run --advertise-host 127.0.0.1 --mine --mine-interval-seconds 15 --status-interval-seconds 2 
```

### Terminal B (observer node)

```bash
RECIP_OUT=$(minichain --data-dir .demo-b wallet generate-key)
RECIP_ADDR=$(echo "$RECIP_OUT" | awk -F= '/address/{gsub(/ /,"",$2); print $2; exit}')

minichain --data-dir .demo-b --host 127.0.0.1 --port 7001 \
  node run --advertise-host 127.0.0.1 --peer 127.0.0.1:7000 --status-interval-seconds 2 
```

### Terminal C (tx submit)



Submit tx to node A:

```bash
minichain --data-dir .demo-a --host 127.0.0.1 --port 7000 tx submit \
  --private-key "$MINER_PK" \
  --recipient "$RECIP_ADDR" \
  --amount 7 \
  --fee 1 \
  --no-mine-now
```

After next mined block on A, verify sync:

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

## 5) True Multi-Device LAN Test

Assume:
- Device A IP: `<A_IP>`
- Device B IP: `<B_IP>`

### Device A (miner)

```bash
minichain --data-dir .demo-a --host 0.0.0.0 --port 7000 --miner-address "$MINER_ADDR" \
  node run --advertise-host <A_IP> --mine --mine-interval-seconds 15 --status-interval-seconds 2 
```

### Device B (observer)

```bash
minichain --data-dir .demo-b --host 0.0.0.0 --port 7001 \
  node run --advertise-host <B_IP> --peer <A_IP>:7000 --status-interval-seconds 2 
```

## 6) Complete CLI Command Reference

Top-level:

```bash
minichain [--host HOST] [--port PORT] [--data-dir DATA_DIR] [--miner-address MINER_ADDRESS] <command>
```

Commands:

```bash
minichain node start
minichain node run [--peer HOST:PORT] [--advertise-host HOST] [--node-id ID] [--mdns] [--mine] [--mine-interval-seconds N] [--sync-batch-size N] [--status-interval-seconds N]
minichain node stop [--timeout-seconds N] [--force]

minichain wallet generate-key
minichain wallet balance --address <20-byte-lowercase-hex>
minichain wallet details --address <20-byte-lowercase-hex>
minichain wallet list [--limit N]

minichain tx submit --private-key <hex-ed25519-key> --recipient <20-byte-lowercase-hex> --amount N [--fee N] [--nonce N] [--mine-now|--no-mine-now]

minichain chain info
minichain chain block (--height N | --hash <block-hash-hex>)
minichain chain accounts [--limit N]

minichain mine [--count N] [--max-transactions N]
minichain shell
```

Useful help commands:

```bash
minichain --help
minichain node --help
minichain node run --help
minichain node stop --help
minichain wallet --help
minichain wallet balance --help
minichain wallet details --help
minichain wallet list --help
minichain tx --help
minichain tx submit --help
minichain chain --help
minichain chain block --help
minichain chain accounts --help
minichain mine --help
```

Legacy aliases (still accepted and auto-remapped):

```bash
start
generate-key
balance
submit-tx
chain-info
block
```



