"""
MiniChain interactive node — testnet demo entry point.

Usage:
    python main.py --port 9000
    python main.py --port 9001 --connect 127.0.0.1:9000

Commands (type in the terminal while the node is running):
    balance                 — show all account balances
    send <to> <amount>      — send coins to another address
    mine                    — mine a block from the mempool
    peers                   — show connected peers
    connect <host>:<port>   — connect to another node
    address                 — show this node's public key
    help                    — show available commands
    quit                    — shut down the node
"""

import argparse
import asyncio
import logging
import os
import re
import sys

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, Blockchain, Block, State, Mempool, P2PNetwork, mine_block
from minichain.persistence import save, load
from minichain.validators import is_valid_receiver


logger = logging.getLogger(__name__)

BURN_ADDRESS = "0" * 40
DATA_DIR = "node_data"


# ──────────────────────────────────────────────
# Wallet helpers
# ──────────────────────────────────────────────

def create_wallet():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


# ──────────────────────────────────────────────
# Block mining
# ──────────────────────────────────────────────

def mine_and_process_block(chain, mempool, miner_pk):
    """Mine pending transactions into a new block."""
    pending_txs = mempool.get_transactions_for_block()
    if not pending_txs:
        logger.info("Mempool is empty — nothing to mine.")
        return None

    # Filter queue candidates against a temporary state snapshot.
    temp_state = chain.state.copy()
    mineable_txs = []
    stale_txs = []
    for tx in pending_txs:
        expected_nonce = temp_state.get_account(tx.sender).get("nonce", 0)
        if tx.nonce < expected_nonce:
            stale_txs.append(tx)
            continue
        if temp_state.validate_and_apply(tx):
            mineable_txs.append(tx)

    if stale_txs:
        mempool.remove_transactions(stale_txs)

    if not mineable_txs:
        logger.info("No mineable transactions in current queue window.")
        return None

    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=mineable_txs,
    )

    mined_block = mine_block(block)

    if chain.add_block(mined_block):
        logger.info("Block #%d mined and added (%d txs)", mined_block.index, len(pending_txs))
        logger.info("✅ Block #%d mined and added (%d txs)", mined_block.index, len(mineable_txs))
        mempool.remove_transactions(mineable_txs)
        chain.state.credit_mining_reward(miner_pk)
        return mined_block
    else:
        logger.error("Block rejected by chain")
        return None


# ──────────────────────────────────────────────
# Network message handler
# ──────────────────────────────────────────────

def make_network_handler(chain, mempool):
    """Return an async callback that processes incoming P2P messages."""

    async def handler(data):
        msg_type = data.get("type")
        payload = data.get("data")

        if msg_type == "sync":
            # Merge remote state into local state (for accounts we don't have yet)
            remote_accounts = payload.get("accounts", {})
            for addr, acc in remote_accounts.items():
                if addr not in chain.state.accounts:
                    chain.state.accounts[addr] = acc
                    logger.info("Synced account %s... (balance=%d)", addr[:12], acc.get("balance", 0))
            logger.info("State sync complete — %d accounts", len(chain.state.accounts))

        elif msg_type == "tx":
            tx = Transaction(**payload)
            if mempool.add_transaction(tx):
                logger.info("Received tx from %s... (amount=%s)", tx.sender[:8], tx.amount)

        elif msg_type == "block":
            txs_raw = payload.get("transactions", [])
            block_hash = payload.get("hash")
            transactions = [Transaction(**t) for t in txs_raw]

            block = Block(
                index=payload["index"],
                previous_hash=payload["previous_hash"],
                transactions=transactions,
                timestamp=payload.get("timestamp"),
                difficulty=payload.get("difficulty"),
            )
            block.nonce = payload.get("nonce", 0)
            block.hash = block_hash

            if chain.add_block(block):
                logger.info("Received Block #%d — added to chain", block.index)

                # Apply mining reward for the remote miner (burn address as placeholder)
                miner = payload.get("miner", BURN_ADDRESS)
                chain.state.credit_mining_reward(miner)

                # Drop only confirmed transactions so higher nonces can remain queued.
                mempool.remove_transactions(block.transactions)
            else:
                logger.warning("Received Block #%s — rejected", block.index)

    return handler


# ──────────────────────────────────────────────
# Interactive CLI
# ──────────────────────────────────────────────

HELP_TEXT = """
╔════════════════════════════════════════════════╗
║              MiniChain Commands                ║
╠════════════════════════════════════════════════╣
║  balance              — show all balances      ║
║  send <to> <amount>   — send coins             ║
║  mine                 — mine a block           ║
║  peers                — show connected peers   ║
║  connect <host:port>  — connect to a peer      ║
║  address              — show your public key   ║
║  chain                — show chain summary     ║
║  help                 — show this help          ║
║  quit                 — shut down               ║
╚════════════════════════════════════════════════╝
"""


async def cli_loop(sk, pk, chain, mempool, network):
    """Read commands from stdin asynchronously."""
    loop = asyncio.get_event_loop()
    print(HELP_TEXT)
    print(f"Your address: {pk}\n")

    while True:
        try:
            raw = await loop.run_in_executor(None, lambda: input("minichain> "))
        except (EOFError, KeyboardInterrupt):
            break

        parts = raw.strip().split()
        if not parts:
            continue
        cmd = parts[0].lower()

        # ── balance ──
        if cmd == "balance":
            accounts = chain.state.accounts
            if not accounts:
                print("  (no accounts yet)")
            for addr, acc in accounts.items():
                tag = " (you)" if addr == pk else ""
                print(f"  {addr[:12]}...  balance={acc['balance']}  nonce={acc['nonce']}{tag}")

        # ── send ──
        elif cmd == "send":
            if len(parts) < 3:
                print("  Usage: send <receiver_address> <amount>")
                continue
            receiver = parts[1]
            if not is_valid_receiver(receiver):
                print("  Invalid receiver format. Expected 40 or 64 hex characters.")
                continue
            try:
                amount = int(parts[2])
            except ValueError:
                print("  Amount must be an integer.")
                continue
            if amount <= 0:
                print("  Amount must be greater than 0.")
                continue

            nonce = chain.state.get_account(pk).get("nonce", 0)
            tx = Transaction(sender=pk, receiver=receiver, amount=amount, nonce=nonce)
            tx.sign(sk)

            if mempool.add_transaction(tx):
                await network.broadcast_transaction(tx)
                print(f"  Tx sent: {amount} coins -> {receiver[:12]}...")
            else:
                print("  Transaction rejected (invalid sig, duplicate, or mempool full).")

        # ── mine ──
        elif cmd == "mine":
            mined = mine_and_process_block(chain, mempool, pk)
            if mined:
                await network.broadcast_block(mined, miner=pk)

        # ── peers ──
        elif cmd == "peers":
            print(f"  Connected peers: {network.peer_count}")

        # ── connect ──
        elif cmd == "connect":
            if len(parts) < 2:
                print("  Usage: connect <host>:<port>")
                continue
            try:
                host, port_str = parts[1].rsplit(":", 1)
                port = int(port_str)
            except ValueError:
                print("  Invalid format. Use host:port")
                continue
            await network.connect_to_peer(host, port)

        # ── address ──
        elif cmd == "address":
            print(f"  {pk}")

        # ── chain ──
        elif cmd == "chain":
            print(f"  Chain length: {len(chain.chain)} blocks")
            for b in chain.chain:
                tx_count = len(b.transactions) if b.transactions else 0
                print(f"    Block #{b.index}  hash={b.hash[:16]}...  txs={tx_count}")

        # ── help ──
        elif cmd == "help":
            print(HELP_TEXT)

        # ── quit ──
        elif cmd in ("quit", "exit", "q"):
            break

        else:
            print(f"  Unknown command: {cmd}. Type 'help' for available commands.")


# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

async def run_node(port: int, connect_to: str | None, fund: int):
    """Boot the node, optionally connect to a peer, then enter the CLI."""
    sk, pk = create_wallet()

    # ── Load existing chain or start fresh ──────────────────────────────────
    chain_file = os.path.join(DATA_DIR, "blockchain.json")
    if os.path.exists(chain_file):
        try:
            chain = load(path=DATA_DIR)
            logger.info("Loaded existing chain (%d blocks) from '%s'", len(chain.chain), DATA_DIR)
        except Exception as e:
            logger.warning("Could not load chain: %s — starting fresh.", e)
            chain = Blockchain()
    else:
        chain = Blockchain()
        logger.info("No saved chain found — starting fresh.")

    mempool = Mempool()
    network = P2PNetwork()

    handler = make_network_handler(chain, mempool)
    network.register_handler(handler)

    # When a new peer connects, send our state so they can sync
    async def on_peer_connected(writer):
        import json as _json
        sync_msg = _json.dumps({
            "type": "sync",
            "data": {"accounts": chain.state.accounts}
        }) + "\n"
        writer.write(sync_msg.encode())
        await writer.drain()
        logger.info("Sent state sync to new peer")

    network._on_peer_connected = on_peer_connected

    await network.start(port=port)

    # Fund this node's wallet so it can transact in the demo
    if fund > 0:
        chain.state.credit_mining_reward(pk, reward=fund)
        logger.info("Funded %s... with %d coins", pk[:12], fund)

    # Connect to a seed peer if requested
    if connect_to:
        try:
            host, peer_port = connect_to.rsplit(":", 1)
            await network.connect_to_peer(host, int(peer_port))
        except ValueError:
            logger.error("Invalid --connect format. Use host:port")

    try:
        await cli_loop(sk, pk, chain, mempool, network)
    finally:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            save(chain, path=DATA_DIR)
            logger.info("Chain saved to '%s' (%d blocks)", DATA_DIR, len(chain.chain))
        except Exception as e:
            logger.error("Failed to save chain: %s", e)
        await network.stop()


def main():
    parser = argparse.ArgumentParser(description="MiniChain Node — Testnet Demo")
    parser.add_argument("--port", type=int, default=9000, help="TCP port to listen on (default: 9000)")
    parser.add_argument("--connect", type=str, default=None, help="Peer address to connect to (host:port)")
    parser.add_argument("--fund", type=int, default=100, help="Initial coins to fund this wallet (default: 100)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(run_node(args.port, args.connect, args.fund))
    except KeyboardInterrupt:
        print("\nNode shut down.")


if __name__ == "__main__":
    main()
