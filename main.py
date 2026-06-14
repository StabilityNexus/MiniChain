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
import re
import sys

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, Blockchain, Block, State, Mempool, P2PNetwork, mine_block
from minichain.rpc import JSONRPCServer
from minichain.validators import is_valid_receiver
from minichain.block import calculate_receipt_root


logger = logging.getLogger(__name__)

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
    receipts = []
    for tx in pending_txs:
        expected_nonce = temp_state.get_account(tx.sender).get("nonce", 0)
        if tx.nonce < expected_nonce:
            stale_txs.append(tx)
            continue
            
        receipt = temp_state.validate_and_apply(tx)
        if receipt is not None:
            mineable_txs.append(tx)
            receipts.append(receipt)

    if stale_txs:
        mempool.remove_transactions(stale_txs)

    if not mineable_txs:
        logger.info("No mineable transactions in current queue window.")
        return None

    total_fees = sum(getattr(r, 'gas_used', 0) for r in receipts)
    temp_state.credit_mining_reward(miner_pk, reward=temp_state.DEFAULT_MINING_REWARD + total_fees)

    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=mineable_txs,
        state_root=temp_state.state_root(),
        receipt_root=calculate_receipt_root(receipts),
        receipts=receipts,
        miner=miner_pk,
    )

    mined_block = mine_block(block)

    if chain.add_block(mined_block):
        logger.info("✅ Block #%d mined and added (%d txs)", mined_block.index, len(mineable_txs))
        mempool.remove_transactions(mineable_txs)
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

def make_network_handler(chain, mempool, network):
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

                # Drop only confirmed transactions so higher nonces can remain queued.
                mempool.remove_transactions(block.transactions)
            else:
                if block.index > chain.last_block.index:
                    logger.warning("📥 Received Block #%s — ahead of us (tip: %s). Requesting chain sync...", block.index, chain.last_block.index)
                    asyncio.create_task(network.broadcast_chain_request())
                else:
                    logger.warning("📥 Received Block #%s — rejected", block.index)

        elif msg_type == "chain_request":
            logger.info("📡 Peer requested chain sync. Broadcasting our chain...")
            blocks_dicts = [b.to_dict() for b in chain.chain]
            payload = {"type": "chain_response", "data": {"blocks": blocks_dicts}}
            asyncio.create_task(network._broadcast_raw(payload))

        elif msg_type == "chain_response":
            blocks_payload = payload.get("blocks", [])
            new_chain = []
            try:
                new_chain = [Block.from_dict(b) for b in blocks_payload]
            except Exception as e:
                logger.warning("❌ Failed to parse chain_response: %s", e)
                return

            if new_chain:
                success, orphans = chain.resolve_conflicts(new_chain)
                if success:
                    logger.info("🔄 Reorg complete! Restoring %d orphaned txs to mempool.", len(orphans))
                    for tx in orphans:
                        mempool.add_transaction(tx)

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
                contract_tag = " [Contract]" if acc.get("code") else ""
                print(f"  {addr[:12]}...  balance={acc['balance']}  nonce={acc['nonce']}{tag}{contract_tag}")

        # ── send ──
        elif cmd == "send":
            if len(parts) < 3:
                print("  Usage: send <receiver_address> <amount> [fee]")
                continue
            receiver = parts[1]
            if not is_valid_receiver(receiver):
                print("  Invalid receiver format. Expected 40 or 64 hex characters.")
                continue
            try:
                amount = int(parts[2])
                fee = int(parts[3]) if len(parts) > 3 else 0
            except ValueError:
                print("  Amount and fee must be integers.")
                continue
            if amount <= 0:
                print("  Amount must be greater than 0.")
                continue
            if fee < 0:
                print("  Fee cannot be negative.")
                continue

            nonce = chain.state.get_account(pk).get("nonce", 0)
            tx = Transaction(sender=pk, receiver=receiver, amount=amount, nonce=nonce, fee=fee)
            tx.sign(sk)

            if mempool.add_transaction(tx):
                await network.broadcast_transaction(tx)
                print(f"  ✅ Tx sent: {amount} coins → {receiver[:12]}...")
            else:
                print("  ❌ Transaction rejected (invalid sig, duplicate, or mempool full).")

        # ── deploy ──
        elif cmd == "deploy":
            if len(parts) < 2:
                print("  Usage: deploy <filepath> [amount] [fee]")
                continue
            filepath = parts[1]
            try:
                with open(filepath, "r") as f:
                    code = f.read()
            except FileNotFoundError:
                print(f"  File not found: {filepath}")
                continue
            
            try:
                amount = int(parts[2]) if len(parts) > 2 else 0
                fee = int(parts[3]) if len(parts) > 3 else 0
            except ValueError:
                print("  Amount and fee must be integers.")
                continue

            if amount < 0 or fee < 0:
                print("  Amount and fee cannot be negative.")
                continue

            nonce = chain.state.get_account(pk).get("nonce", 0)
            tx = Transaction(sender=pk, receiver=None, amount=amount, nonce=nonce, fee=fee, data=code)
            tx.sign(sk)

            if mempool.add_transaction(tx):
                await network.broadcast_transaction(tx)
                print(f"  ✅ Deploy Tx sent (nonce={nonce}). Mine a block to confirm.")
            else:
                print("  ❌ Deploy Transaction rejected.")

        # ── call ──
        elif cmd == "call":
            if len(parts) < 3:
                print("  Usage: call <contract_address> <payload> [amount] [fee]")
                continue
            receiver = parts[1]
            if not is_valid_receiver(receiver):
                print("  Invalid receiver format. Expected 40 or 64 hex characters.")
                continue
            payload = parts[2]
            
            try:
                amount = int(parts[3]) if len(parts) > 3 else 0
                fee = int(parts[4]) if len(parts) > 4 else 0
            except ValueError:
                print("  Amount and fee must be integers.")
                continue

            if amount < 0 or fee < 0:
                print("  Amount and fee cannot be negative.")
                continue

            nonce = chain.state.get_account(pk).get("nonce", 0)
            tx = Transaction(sender=pk, receiver=receiver, amount=amount, nonce=nonce, fee=fee, data=payload)
            tx.sign(sk)

            if mempool.add_transaction(tx):
                await network.broadcast_transaction(tx)
                print(f"  ✅ Call Tx sent to {receiver[:12]}... (payload='{payload}'). Mine a block to confirm.")
            else:
                print("  ❌ Call Transaction rejected.")

        # ── mine ──
        elif cmd == "mine":
            mined = mine_and_process_block(chain, mempool, pk)
            if mined:
                await network.broadcast_block(mined)  # ← just this, no miner assignment above it

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
    if datadir:
        try:
            from minichain.persistence import load, persistence_exists
            if persistence_exists(datadir):
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

    handler = make_network_handler(chain, mempool, network)
    network.register_handler(handler)
    
    rpc_server = JSONRPCServer(chain, mempool, network)

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
    
    # Start RPC server on a port correlated to the node port (e.g. 8545 if P2P is 9000)
    rpc_port = 8545 + (port - 9000)
    rpc_task = asyncio.create_task(rpc_server.start(host="127.0.0.1", port=rpc_port))

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
        
        if rpc_task:
            rpc_task.cancel()
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
