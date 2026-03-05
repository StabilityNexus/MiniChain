"""
MiniChain interactive node — testnet demo entry point.

Usage:
    python main.py --port 9000 --mine
    python main.py --port 9001 --connect 127.0.0.1:9000

Commands (type in the terminal while the node is running):
    balance                 — show all account balances
    send <to> <amount>      — send coins to another address
    mine                    — mine a block from the mempool (or toggle auto-mine)
    peers                   — show connected peers
    connect <host>:<port>   — connect to another node
    address                 — show this node's public key
    sync                    — request chain sync from peers
    help                    — show available commands
    quit                    — shut down the node
"""

import argparse
import asyncio
import logging
import re
import sys
import time
import threading

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, Blockchain, Block, State, Mempool, P2PNetwork, mine_block


logger = logging.getLogger(__name__)

BURN_ADDRESS = "0" * 40


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

    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=pending_txs,
    )

    mined_block = mine_block(block)

    if chain.add_block(mined_block):
        logger.info("✅ Block #%d mined and added (%d txs)", mined_block.index, len(pending_txs))
        chain.state.credit_mining_reward(miner_pk)
        return mined_block
    else:
        logger.error("❌ Block rejected by chain")
        return None


# ──────────────────────────────────────────────
# Network message handler
# ──────────────────────────────────────────────

def make_network_handler(chain, mempool, network):
    """Return an async callback that processes incoming P2P messages."""

    async def handler(data, writer=None):
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

        elif msg_type == "get_chain":
            # Peer is requesting our chain
            if writer:
                chain_data = chain.to_dict_list()
                await network.send_chain(writer, chain_data)
                logger.info("📤 Sent chain (%d blocks) to peer", len(chain_data))

        elif msg_type == "chain":
            # Received a chain from peer
            chain_data = payload if isinstance(payload, list) else []
            if chain_data:
                logger.info("📥 Received chain with %d blocks", len(chain_data))
                if chain.replace_chain(chain_data):
                    logger.info("🔄 Replaced chain with received chain (new height: %d)", len(chain.chain))
                else:
                    logger.info("🔄 Chain validation: keeping our chain")

        elif msg_type == "tx":
            tx = Transaction(**payload)
            if mempool.add_transaction(tx):
                logger.info("📥 Received tx from %s... (amount=%s)", tx.sender[:8], tx.amount)

        elif msg_type == "block":
            txs_raw = payload.pop("transactions", [])
            block_hash = payload.pop("hash", None)
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

                # Remove only the transactions present in this block
                tx_ids = [mempool.get_tx_id_from_dict(t) for t in txs_raw]
                mempool.remove_transactions_by_ids(tx_ids)
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
║  mine                 — mine a block / toggle  ║
║  peers                — show connected peers   ║
║  connect <host:port>  — connect to a peer      ║
║  address              — show your public key   ║
║  chain                — show chain summary     ║
║  sync                 — sync chain from peers  ║
║  help                 — show this help          ║
║  quit                 — shut down               ║
╚════════════════════════════════════════════════╝
"""


async def cli_loop(sk, pk, chain, mempool, network, nonce_counter, mining_enabled):
    """Read commands from stdin asynchronously with optional auto-mining."""
    loop = asyncio.get_event_loop()
    print(HELP_TEXT)
    print(f"Your address: {pk}\n")

    mine_interval = 10  # seconds between auto-mine attempts
    running = True
    input_queue = asyncio.Queue()
    stop_event = threading.Event()

    async def auto_mine_loop():
        """Background task for automatic mining."""
        while running:
            if mining_enabled[0]:
                pending_preview = mempool.get_pending_transactions()
                # Create coinbase transaction for mining reward
                coinbase_tx = Transaction(
                    sender=State.COINBASE_ADDRESS,
                    receiver=pk,
                    amount=50,
                    nonce=0,
                    data=None,
                    signature=None,
                )
                if pending_preview:
                    pending_txs = mempool.get_transactions_for_block()
                else:
                    pending_txs = []
                all_txs = [coinbase_tx] + pending_txs
                
                block = Block(
                    index=chain.last_block.index + 1,
                    previous_hash=chain.last_block.hash,
                    transactions=all_txs,
                )
                
                try:
                    mined_block = mine_block(block, difficulty=4, timeout_seconds=5)
                    if chain.add_block(mined_block):
                        logger.info("⛏️ Mined block #%d! Hash: %s...", mined_block.index, mined_block.hash[:16])
                        await network.broadcast_block(mined_block)
                except Exception:
                    # Mining timeout or error - return transactions to mempool
                    if pending_txs:
                        result = mempool.restore_transactions(pending_txs)
                        if result.get("dropped"):
                            logger.warning("Mempool restore dropped %d txs", result["dropped"])
            
            await asyncio.sleep(mine_interval)

    def input_reader_thread():
        """Blocking input reader running in a daemon thread."""
        while not stop_event.is_set():
            try:
                raw = input("minichain> ")
            except (EOFError, KeyboardInterrupt):
                loop.call_soon_threadsafe(input_queue.put_nowait, None)
                break
            loop.call_soon_threadsafe(input_queue.put_nowait, raw)

    # Start background tasks
    mine_task = asyncio.create_task(auto_mine_loop())
    input_thread = threading.Thread(
        target=input_reader_thread,
        name="minichain-input",
        daemon=True,
    )
    input_thread.start()

    try:
        while True:
            try:
                raw = await input_queue.get()
                if raw is None:
                    break
            except Exception:
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
                try:
                    amount = int(parts[2])
                except ValueError:
                    print("  Amount must be an integer.")
                    continue

                nonce = nonce_counter[0]
                tx = Transaction(sender=pk, receiver=receiver, amount=amount, nonce=nonce)
                tx.sign(sk)

                if mempool.add_transaction(tx):
                    nonce_counter[0] += 1
                    await network.broadcast_transaction(tx)
                    print(f"  ✅ Tx sent: {amount} coins → {receiver[:12]}...")
                else:
                    print("  ❌ Transaction rejected (invalid sig, duplicate, or mempool full).")

            # ── mine ──
            elif cmd == "mine":
                if len(parts) > 1 and parts[1] == "toggle":
                    mining_enabled[0] = not mining_enabled[0]
                    print(f"  Auto-mining: {'ON' if mining_enabled[0] else 'OFF'}")
                else:
                    # Manual mine
                    mined = mine_and_process_block(chain, mempool, pk)
                    if mined:
                        await network.broadcast_block(mined)
                        # Sync local nonce from chain state
                        acc = chain.state.get_account(pk)
                        nonce_counter[0] = acc.get("nonce", 0)
                    print(f"  Auto-mining: {'ON' if mining_enabled[0] else 'OFF'} (use 'mine toggle' to switch)")

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

            # ── sync ──
            elif cmd == "sync":
                if network.peer_count == 0:
                    print("  No peers connected. Connect to a peer first.")
                else:
                    print(f"  Requesting chain from {network.peer_count} peer(s)...")
                    await network.request_chain()

            # ── help ──
            elif cmd == "help":
                print(HELP_TEXT)

            # ── quit ──
            elif cmd in ("quit", "exit", "q"):
                break

            else:
                print(f"  Unknown command: {cmd}. Type 'help' for available commands.")
    finally:
        # Cleanup background tasks
        running = False
        stop_event.set()
        try:
            loop.call_soon_threadsafe(input_queue.put_nowait, None)
        except Exception:
            pass
        mine_task.cancel()
        try:
            await mine_task
        except asyncio.CancelledError:
            pass


# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

async def run_node(port: int, connect_to: str | None, fund: int, auto_mine: bool = False):
    """Boot the node, optionally connect to a peer, then enter the CLI."""
    sk, pk = create_wallet()

    chain = Blockchain()
    mempool = Mempool()
    network = P2PNetwork()

    handler = make_network_handler(chain, mempool, network)
    network.register_handler(handler)

    # When a new peer connects, send our chain so they can sync
    async def on_peer_connected(writer):
        chain_data = chain.to_dict_list()
        await network.send_chain(writer, chain_data)
        logger.info("🔄 Sent chain (%d blocks) to new peer", len(chain_data))

    network._on_peer_connected = on_peer_connected

    await network.start(port=port)

    # Fund this node's wallet so it can transact in the demo
    if fund > 0:
        chain.state.credit_mining_reward(pk, reward=fund)
        logger.info("💰 Funded %s... with %d coins", pk[:12], fund)

    # Connect to a seed peer if requested
    if connect_to:
        try:
            host, peer_port = connect_to.rsplit(":", 1)
            connected = await network.connect_to_peer(host, int(peer_port))
            if connected:
                # Request chain sync from the peer we just connected to
                await network.request_chain()
        except ValueError:
            logger.error("Invalid --connect format. Use host:port")

    # Nonce counter kept as a mutable list so the CLI closure can mutate it
    nonce_counter = [0]
    # Auto-mine flag as mutable list
    mining_enabled = [auto_mine]
    if auto_mine:
        logger.info("⛏️ Auto-mining enabled")

    try:
        await cli_loop(sk, pk, chain, mempool, network, nonce_counter, mining_enabled)
    finally:
        await network.stop()


def main():
    parser = argparse.ArgumentParser(description="MiniChain Node — Testnet Demo")
    parser.add_argument("--port", type=int, default=9000, help="TCP port to listen on (default: 9000)")
    parser.add_argument("--connect", type=str, default=None, help="Peer address to connect to (host:port)")
    parser.add_argument("--fund", type=int, default=100, help="Initial coins to fund this wallet (default: 100)")
    parser.add_argument("--mine", action="store_true", help="Enable automatic mining")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(run_node(args.port, args.connect, args.fund, args.mine))
    except KeyboardInterrupt:
        print("\nNode shut down.")


if __name__ == "__main__":
    main()
