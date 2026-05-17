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



logger = logging.getLogger(__name__)

BURN_ADDRESS = "0" * 40
TRUSTED_PEERS = set()
LOCALHOST_PEERS = {"127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1"}


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
        if temp_state.apply_transaction(tx):
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
        logger.info("✅ Block #%d mined and added (%d txs)", mined_block.index, len(mineable_txs))
        mempool.remove_transactions(mineable_txs)
        chain.state.credit_mining_reward(miner_pk)
        return mined_block
    else:
        logger.error("❌ Block rejected by chain")
        restored = 0
        for tx in pending_txs:
            if mempool.add_transaction(tx):
                restored += 1
        logger.info("Mempool: Restored %d/%d txs after rejection", restored, len(pending_txs))
        return None


# ──────────────────────────────────────────────
# Network message handler
# ──────────────────────────────────────────────

def make_network_handler(chain, mempool):
    """Return an async callback that processes incoming P2P messages."""

    async def handler(data):
        msg_type = data.get("type")
        payload = data.get("data")
        peer_addr = data.get("_peer_addr", "unknown")

        if msg_type == "sync":
            peer_host = peer_addr.rsplit(":", 1)[0] if ":" in peer_addr else peer_addr
            peer_host = peer_host.strip("[]")
            is_trusted = peer_addr in TRUSTED_PEERS or peer_host in TRUSTED_PEERS
            is_localhost = peer_host in LOCALHOST_PEERS
            if chain.state.accounts and not (is_trusted or is_localhost):
                logger.warning("🔒 Rejected sync from untrusted peer %s", peer_addr)
                return

            # Merge remote state into local state (for accounts we don't have yet)
            remote_accounts = payload.get("accounts") if isinstance(payload, dict) else None
            if not isinstance(remote_accounts, dict):
                logger.warning("🔒 Rejected sync from %s with invalid accounts payload", peer_addr)
                return

            for addr, acc in remote_accounts.items():
                if not isinstance(acc, dict):
                    logger.warning("🔒 Skipping malformed account %r from %s", addr, peer_addr)
                    continue
                if addr not in chain.state.accounts:
                    chain.state.accounts[addr] = acc
                    logger.info("🔄 Synced account %s... (balance=%d)", addr[:12], acc.get("balance", 0))
            logger.info("🔄 Accepted state sync from %s — %d accounts", peer_addr, len(chain.state.accounts))

        elif msg_type == "tx":
            tx = Transaction.from_dict(payload)
            if mempool.add_transaction(tx):
                logger.info("📥 Received tx from %s... (amount=%s)", tx.sender[:8], tx.amount)

        elif msg_type == "block":
            block = Block.from_dict(payload)

            if chain.add_block(block):
                logger.info("📥 Received Block #%d — added to chain", block.index)

                # Apply mining reward for the remote miner (burn address as placeholder)
                miner = payload.get("miner", BURN_ADDRESS)
                chain.state.credit_mining_reward(miner)

                # Drop only confirmed transactions so higher nonces can remain queued.
                mempool.remove_transactions(block.transactions)
            else:
                logger.warning("📥 Received Block #%s — rejected", block.index)

    return handler


# ──────────────────────────────────────────────
# Interactive CLI
# ──────────────────────────────────────────────

HELP_TEXT = """
╔════════════════════════════════════════════════╗
║              MiniChain Commands                ║
╠════════════════════════════════════════════════╣
║  balance              - show all balances      ║
║  send <to> <amount>   - send coins             ║
║  mine                 - mine a block           ║
║  peers                - show connected peers   ║
║  connect <host>:<port> - connect to a peer     ║
║  address              - show your public key   ║
║  chain                - show chain summary     ║
║  help                 - show this help         ║
║  quit                 - shut down              ║
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
            if not Transaction.is_valid_address(receiver):
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
                print(f"  ✅ Tx sent: {amount} coins → {receiver[:12]}...")
            else:
                print("  ❌ Transaction rejected (invalid sig, duplicate, or mempool full).")

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
            success = await network.connect_to_peer(host, port)
            if success:
                print(f"  Connected to {host}:{port}")
            else:
                print(f"  Failed to connect to {host}:{port}")

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

async def run_node(port: int, host: str, connect_to: str | None, fund: int, datadir: str | None):
    """Boot the node, optionally connect to a peer, then enter the CLI."""
    sk, pk = create_wallet()

    # Load existing chain from disk, or start fresh
    chain = None
    if datadir and os.path.exists(os.path.join(datadir, "data.json")):
        try:
            from minichain.persistence import load
            chain = load(datadir)
            logger.info("Restored chain from '%s'", datadir)
        except FileNotFoundError as e:
            logger.warning("Could not load saved chain: %s — starting fresh", e)
        except ValueError as e:
            logger.error("State data is corrupted or tampered: %s", e)
            logger.error("Refusing to start to avoid overwriting corrupted data.")
            sys.exit(1)

    if chain is None:
        chain = Blockchain()

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
        logger.info("🔄 Sent state sync to new peer")

    network.register_on_peer_connected(on_peer_connected)

    await network.start(port=port, host=host)

    # Fund this node's wallet so it can transact in the demo
    if fund > 0:
        chain.state.credit_mining_reward(pk, reward=fund)
        logger.info("💰 Funded %s... with %d coins", pk[:12], fund)

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
        # Save chain to disk on shutdown
        if datadir:
            try:
                from minichain.persistence import save
                save(chain, datadir)
                logger.info("Chain saved to '%s'", datadir)
            except Exception as e:
                logger.error("Failed to save chain during shutdown: %s", e)
        await network.stop()


def main():
    parser = argparse.ArgumentParser(description="MiniChain Node — Testnet Demo")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host/IP to bind the P2P server (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="TCP port to listen on (default: 9000)")
    parser.add_argument("--connect", type=str, default=None, help="Peer address to connect to (host:port)")
    parser.add_argument("--fund", type=int, default=100, help="Initial coins to fund this wallet (default: 100)")
    parser.add_argument("--datadir", type=str, default=None, help="Directory to save/load blockchain state (enables persistence)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(run_node(args.port, args.host, args.connect, args.fund, args.datadir))
    except KeyboardInterrupt:
        print("\nNode shut down.")


if __name__ == "__main__":
    main()
