"""
MiniChain interactive node — testnet demo entry point.
"""

import argparse
import asyncio
import logging
import os
import sys

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, Blockchain, Block, Mempool, P2PNetwork, mine_block
from minichain.validators import is_valid_receiver

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
# Block mining (FIXED per requirements)
# ──────────────────────────────────────────────

def mine_and_process_block(chain, mempool, miner_pk):
    """Mine pending transactions into a new block."""
    pending_txs = mempool.get_transactions_for_block()
    if not pending_txs:
        logger.info("Mempool is empty — nothing to mine.")
        return None

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

    #  REQUIREMENT: Pass chain.difficulty into the Block constructor
    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=mineable_txs,
        difficulty=chain.difficulty  # Passed here directly
    )

    #  REQUIREMENT: mine_block() called inside this function
    mined_block = mine_block(block, difficulty=block.difficulty)

    if chain.add_block(mined_block):
        logger.info(" Block #%d mined and added (%d txs)", mined_block.index, len(mineable_txs))
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
    async def handler(data):
        msg_type = data.get("type")
        payload = data.get("data")

        if msg_type == "sync":
            remote_accounts = payload.get("accounts", {})
            for addr, acc in remote_accounts.items():
                if addr not in chain.state.accounts:
                    chain.state.accounts[addr] = acc
            logger.info(" State sync complete — %d accounts", len(chain.state.accounts))

        elif msg_type == "tx":
            tx = Transaction(**payload)
            if mempool.add_transaction(tx):
                logger.info("📥 Received tx from %s...", tx.sender[:8])

        elif msg_type == "block":
            txs_raw = payload.get("transactions", [])
            transactions = [Transaction(**t) for t in txs_raw]

            block = Block(
                index=payload["index"],
                previous_hash=payload["previous_hash"],
                transactions=transactions,
                timestamp=payload.get("timestamp"),
                difficulty=payload.get("difficulty"),
            )
            block.nonce = payload.get("nonce", 0)
            block.hash = payload.get("hash")

            if chain.add_block(block):
                logger.info("📥 Received Block #%d — added to chain", block.index)
                miner = payload.get("miner", BURN_ADDRESS)
                chain.state.credit_mining_reward(miner)
                mempool.remove_transactions(block.transactions)

    return handler

# ──────────────────────────────────────────────
# Interactive CLI
# ──────────────────────────────────────────────

HELP_TEXT = """
╔════════════════════════════════════════════════╗
║             MiniChain Commands                 ║
╠════════════════════════════════════════════════╣
║  balance       — show all balances             ║
║  send <to> <#> — send coins                    ║
║  mine          — mine a block                  ║
║  peers         — show connected peers          ║
║  connect <h:p> — connect to a peer             ║
║  address       — show your public key          ║
║  chain         — show chain summary            ║
║  help          — show this help                ║
║  quit          — shut down                     ║
╚════════════════════════════════════════════════╝
"""

async def cli_loop(sk, pk, chain, mempool, network):
    loop = asyncio.get_event_loop()
    print(HELP_TEXT)
    print(f"Your address: {pk}\n")

    while True:
        try:
            raw = await loop.run_in_executor(None, lambda: input("minichain> "))
            parts = raw.strip().split()
            if not parts: continue
            cmd = parts[0].lower()

            if cmd == "balance":
                for addr, acc in chain.state.accounts.items():
                    tag = " (you)" if addr == pk else ""
                    print(f"  {addr[:12]}... balance={acc['balance']} nonce={acc['nonce']}{tag}")

            elif cmd == "send":
                if len(parts) < 3: continue
                receiver, amount = parts[1], int(parts[2])
                nonce = chain.state.get_account(pk).get("nonce", 0)
                tx = Transaction(sender=pk, receiver=receiver, amount=amount, nonce=nonce)
                tx.sign(sk)
                if mempool.add_transaction(tx):
                    await network.broadcast_transaction(tx)
                    print(f"   Tx sent: {amount} coins")

            elif cmd == "mine":
                mined = mine_and_process_block(chain, mempool, pk)
                if mined:
                    await network.broadcast_block(mined, miner=pk)

            elif cmd == "peers":
                print(f"  Connected peers: {network.peer_count}")

            elif cmd == "connect":
                host, port = parts[1].rsplit(":", 1)
                await network.connect_to_peer(host, int(port))

            elif cmd == "address":
                print(f"  {pk}")

            elif cmd == "chain":
                print(f"  Chain length: {len(chain.chain)} blocks")

            elif cmd == "help":
                print(HELP_TEXT)

            elif cmd in ("quit", "exit", "q"):
                break
        except Exception as e:
            print(f"  Error: {e}")

# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

async def run_node(port: int, connect_to: str | None, fund: int, datadir: str | None):
    sk, pk = create_wallet()
    chain = None

    #  REQUIREMENT: Preserve persistence.py we added
    if datadir and os.path.exists(os.path.join(datadir, "data.json")):
        try:
            from minichain.persistence import load
            chain = load(datadir)
            logger.info("Restored chain from '%s'", datadir)
        except Exception as e:
            logger.warning("Could not load saved chain: %s — starting fresh", e)

    if chain is None:
        chain = Blockchain()

    mempool = Mempool()
    network = P2PNetwork()
    handler = make_network_handler(chain, mempool)
    network.register_handler(handler)

    # State sync on connection
    async def on_peer_connected(writer):
        import json
        sync_msg = json.dumps({"type": "sync", "data": {"accounts": chain.state.accounts}}) + "\n"
        writer.write(sync_msg.encode())
        await writer.drain()

    network._on_peer_connected = on_peer_connected
    await network.start(port=port)

    if fund > 0:
        chain.state.credit_mining_reward(pk, reward=fund)

    if connect_to:
        host, p = connect_to.rsplit(":", 1)
        await network.connect_to_peer(host, int(p))

    try:
        await cli_loop(sk, pk, chain, mempool, network)
    finally:
        #  REQUIREMENT: Save on shutdown
        if datadir:
            from minichain.persistence import save
            save(chain, datadir)
            logger.info("Chain saved.")
        await network.stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--connect", type=str, default=None)
    parser.add_argument("--fund", type=int, default=100)
    parser.add_argument("--datadir", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    try:
        asyncio.run(run_node(args.port, args.connect, args.fund, args.datadir))
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
