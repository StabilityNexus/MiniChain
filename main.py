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
import time
from nacl.signing import SigningKey
import nacl.encoding
from nacl.encoding import HexEncoder

from minichain import Transaction, Blockchain, Block, State, Mempool, P2PNetwork, mine_block
from minichain.validators import is_valid_receiver

# Local project imports
from minichain import Transaction, Blockchain, Block, mine_block, Mempool, P2PNetwork

logger = logging.getLogger(__name__)

BURN_ADDRESS = "0" * 40


# -------------------------
# Wallet Creation
# -------------------------
def create_wallet():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=nacl.encoding.HexEncoder).decode()
    return sk, pk


# -------------------------
# Mining + Block Processing
# -------------------------
def mine_and_process_block(chain, mempool, pending_nonce_map):
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

   # Mine using current consensus difficulty; chain updates next difficulty after acceptance
    block.difficulty = chain.difficulty

    start_time = time.time()
    mined_block = mine_block(block, difficulty=block.difficulty)
    mining_time = time.time() - start_time

    # Attach mining time to block (optional but useful)
    mined_block.mining_time = mining_time

    if not hasattr(mined_block, "miner"):
        mined_block.miner = BURN_ADDRESS

    deployed_contracts = []

    if chain.add_block(mined_block):
        logger.info("Block #%s added with Difficulty: %s",
                    mined_block.index,
                    mined_block.difficulty)

        # Reward miner
        miner_attr = getattr(mined_block, "miner", BURN_ADDRESS)
        miner_address = (
            miner_attr if re.match(r'^[0-9a-fA-F]{40}$', str(miner_attr))
            else BURN_ADDRESS
        )

        chain.state.credit_mining_reward(miner_address)

        for tx in mined_block.transactions:
            sync_nonce(chain.state, pending_nonce_map, tx.sender)

            result = chain.state.get_account(tx.receiver) if tx.receiver else None
            if isinstance(result, dict):
                deployed_contracts.append(tx.receiver)

        return mined_block, deployed_contracts

        logger.info("✅ Block #%d mined and added (%d txs)", mined_block.index, len(mineable_txs))
        mempool.remove_transactions(mineable_txs)
        chain.state.credit_mining_reward(miner_pk)
        return mined_block
    else:
        logger.error("❌ Block rejected by chain")
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
                    logger.info("🔄 Synced account %s... (balance=%d)", addr[:12], acc.get("balance", 0))
            logger.info("🔄 State sync complete — %d accounts", len(chain.state.accounts))

        elif msg_type == "tx":
            tx = Transaction(**payload)
            if mempool.add_transaction(tx):
                logger.info("📥 Received tx from %s... (amount=%s)", tx.sender[:8], tx.amount)

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

# -------------------------
# Nonce Sync
# -------------------------
def sync_nonce(state, pending_nonce_map, address):
    account = state.get_account(address)
    pending_nonce_map[address] = account.get("nonce", 0) if account else 0

# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

# -------------------------
# Node Logic
# -------------------------
async def node_loop():
    logger.info("Starting MiniChain Node with PID Difficulty Adjustment")
async def run_node(port: int, connect_to: str | None, fund: int, datadir: str | None):
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
    pending_nonce_map = {}

    def get_next_nonce(address) -> int:
        account = chain.state.get_account(address)
        account_nonce = account.get("nonce", 0) if account else 0
        local_nonce = pending_nonce_map.get(address, account_nonce)
        next_nonce = max(account_nonce, local_nonce)
        pending_nonce_map[address] = next_nonce + 1
        return next_nonce

    async def _handle_network_data(data):
        try:
            if data["type"] == "tx":
                tx = Transaction(**data["data"])
                if mempool.add_transaction(tx):
                    await network.broadcast_transaction(tx)

            elif data["type"] == "block":
                block_data = data["data"]
                txs = [
                    Transaction(**tx_d)
                    for tx_d in block_data.get("transactions", [])
                ]

                block = Block(
                    index=block_data["index"],
                    previous_hash=block_data["previous_hash"],
                    transactions=txs,
                    timestamp=block_data.get("timestamp"),
                    difficulty=block_data.get("difficulty"),
                )
                 block.nonce = block_data.get("nonce", 0)
                 block.hash = block_data.get("hash")
                 block.miner = block_data.get("miner", BURN_ADDRESS)

      chain.add_block(block)
         except Exception:
             logger.exception("Network error while handling incoming data")

    # Nonce counter kept as a mutable list so the CLI closure can mutate it
    nonce_counter = [0]

    try:
        await _run_node(network, chain, mempool, pending_nonce_map, get_next_nonce)
    finally:
        await network.stop()


# -------------------------
# Run Node
# -------------------------
async def _run_node(network, chain, mempool, pending_nonce_map, get_next_nonce):
    await network.start()

    alice_sk, alice_pk = create_wallet()
    _bob_sk, bob_pk = create_wallet()

    # Initial funding
    chain.state.credit_mining_reward(alice_pk, reward=100)
    sync_nonce(chain.state, pending_nonce_map, alice_pk)

    # Alice sends Bob 10 coins
    logger.info("[2] Alice sending 10 coins to Bob")

    # -------------------------------
    # PID Demo: Mining 5 Blocks
    # -------------------------------
    logger.info("[3] Mining Multiple Blocks (Watch Difficulty Adjust)")

    for i in range(5):
        await asyncio.sleep(1)
        tx_payment = Transaction(
             sender=alice_pk,
             receiver=bob_pk,
             amount=10,
             nonce=get_next_nonce(alice_pk),
         )
         tx_payment.sign(alice_sk)
         mempool.add_transaction(tx_payment)

        logger.info(f"\nMining Block {i+1}")

        mined = mine_and_process_block(chain, mempool, pending_nonce_map)
         if not mined:
             logger.info("No pending transactions to mine in this iteration")
             continue
         mined_block, _ = mined
        

        if mined_block:
            logger.info("Block mined in %.2f seconds",
                        mined_block.mining_time)

            logger.info("New difficulty: %s",
                        chain.difficulty)

    # Final balances
    alice_acc = chain.state.get_account(alice_pk)
    bob_acc = chain.state.get_account(bob_pk)

    logger.info(
        "Final Balances -> Alice: %s, Bob: %s",
        alice_acc.get("balance", 0),
        bob_acc.get("balance", 0),
    )


# -------------------------
# Entry Point
# -------------------------

async def start_interactive_node(port=None, connect=None):
    chain = Blockchain()
    mempool = Mempool()
    network = P2PNetwork()
    pending_nonce_map = {}

    sk, pk = create_wallet()

    nonce_counter = [0]

    await network.start(port=port)

    if connect:
        host, port_str = connect.rsplit(":", 1)
        await network.connect_to_peer(host, int(port_str))

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


async def run_demo():
    chain = Blockchain()
    mempool = Mempool()
    network = P2PNetwork()
    pending_nonce_map = {}

    await network.start()

    def get_next_nonce(address):
        account = chain.state.get_account(address)
        account_nonce = account.get("nonce", 0) if account else 0
        local_nonce = pending_nonce_map.get(address, account_nonce)
        next_nonce = max(account_nonce, local_nonce)
        pending_nonce_map[address] = next_nonce + 1
        return next_nonce

    try:
        await _run_node(network, chain, mempool, pending_nonce_map, get_next_nonce)
    finally:
        await network.stop()


def main():
    parser = argparse.ArgumentParser(description="MiniChain Node")

    parser.add_argument("--port", type=int, help="Port to run node")
    parser.add_argument("--connect", help="Peer to connect to host:port")
    parser.add_argument("--demo", action="store_true", help="Run Alice/Bob demo")

    parser = argparse.ArgumentParser(description="MiniChain Node — Testnet Demo")
    parser.add_argument("--port", type=int, default=9000, help="TCP port to listen on (default: 9000)")
    parser.add_argument("--connect", type=str, default=None, help="Peer address to connect to (host:port)")
    parser.add_argument("--fund", type=int, default=100, help="Initial coins to fund this wallet (default: 100)")
    parser.add_argument("--datadir", type=str, default=None, help="Directory to save/load blockchain state (enables persistence)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        if args.demo:
            asyncio.run(run_demo())
        else:
            asyncio.run(start_interactive_node(args.port, args.connect))
        asyncio.run(run_node(args.port, args.connect, args.fund, args.datadir))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
