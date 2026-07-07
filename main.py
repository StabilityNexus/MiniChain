"""
MiniChain interactive node — testnet demo entry point.

Usage:
    python main.py --port 9000
    python main.py --port 9001 --connect <multiaddress>

Commands (type in the terminal while the node is running):
    balance                 — show all account balances
    send <to> <amount>      - send coins                        
    deploy <file>           - deploy a contract                 
    call <addr> <data>      - call a contract                   
    mine                    — mine a block from the mempool
    peers                   — show connected peers
    connect <multiaddr>     — connect to another node
    address                 — show this node's public key
    help                    — show available commands
    quit                    — shut down the node
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import os
import json

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, Blockchain, Block, State, Mempool, P2PNetwork, mine_block
from minichain.rpc import JSONRPCServer
from minichain.validators import is_valid_receiver, ValidationStatus
from minichain.block import calculate_receipt_root


logger = logging.getLogger(__name__)

TRUSTED_PEERS = set()
LOCALHOST_PEERS = {"127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1"}


# ──────────────────────────────────────────────
# Wallet helpers
# ──────────────────────────────────────────────

def load_or_create_wallet(datadir: str | None):
    path = datadir or "."
    keystore_path = os.path.join(path, "keystore.json")

    # Security Warning:
    # Keys are currently stored in unencrypted raw hex format for minimality.
    # In a production environment, this file should be encrypted with a "spending password"
    # so that the private key only lives in memory when decrypted by the user.
    if os.path.exists(keystore_path):
        try:
            with open(keystore_path, "r") as f:
                data = json.load(f)
                sk_hex = data.get("private_key")
                if sk_hex:
                    sk = SigningKey(bytes.fromhex(sk_hex))
                    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
                    logger.info("Loaded existing wallet from %s", keystore_path)
                    return sk, pk
        except Exception as e:
            logger.warning("Failed to load keystore: %s", e)

    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    
    os.makedirs(path, exist_ok=True)
    try:
        with open(keystore_path, "w") as f:
            json.dump({"private_key": sk.encode(encoder=HexEncoder).decode()}, f)
        logger.info("Created new wallet at %s", keystore_path)
    except Exception as e:
        logger.warning("Failed to save keystore: %s", e)

    return sk, pk


def request_chain(network, start_index, limit):
    """Broadcast a chain_request for blocks starting at start_index."""
    req = {"type": "chain_request", "data": {"start_index": start_index, "limit": limit}}
    asyncio.create_task(network._broadcast_raw(req))


def parse_tx_params(parts, start):
    """Parse optional non-negative amount, gas_limit, max_fee_per_gas.
    Returns (amount, gas_limit, max_fee), or None if invalid."""
    try:
        amount = int(parts[start]) if len(parts) > start else 0
        gas_limit = int(parts[start + 1]) if len(parts) > start + 1 else 0
        max_fee = int(parts[start + 2]) if len(parts) > start + 2 else 0
    except ValueError:
        print("  Values must be integers.")
        return None
    if amount < 0 or gas_limit < 0 or max_fee < 0:
        print("  Values cannot be negative.")
        return None
    return amount, gas_limit, max_fee


async def submit_and_broadcast(mempool, network, tx, ok_msg, reject_msg):
    """Add a signed tx to the mempool and broadcast it, printing the outcome."""
    if mempool.add_transaction(tx):
        await network.broadcast_transaction(tx)
        print(ok_msg)
    else:
        print(reject_msg)


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

    total_fees = sum(getattr(r, 'gas_used', 0) * getattr(tx, 'max_fee_per_gas', 0) for r, tx in zip(receipts, mineable_txs))
    temp_state.credit_mining_reward(miner_pk, reward=temp_state.DEFAULT_MINING_REWARD + total_fees)

    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=mineable_txs,
        state_root=temp_state.state_root(),
        receipt_root=calculate_receipt_root(receipts),
        receipts=receipts,
        miner=miner_pk,
        difficulty=chain.current_difficulty,
    )

    mined_block = mine_block(block)

    if chain.add_block(mined_block) == ValidationStatus.VALID:
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

        if payload is None and msg_type in ("hello", "chain_request", "chain_response"):
            return

        if msg_type == "hello":
            peer_chain_id = payload.get("chain_id")
            peer_gen_hash = payload.get("genesis_hash")
            if peer_chain_id != chain.chain_id:
                logger.warning("🔒 Disconnecting peer %s: chain_id mismatch (got %s, expected %s)", peer_addr, peer_chain_id, chain.chain_id)
                asyncio.create_task(network.disconnect_peer(peer_addr))
                return
            if peer_gen_hash != chain.chain[0].hash:
                logger.warning("🔒 Disconnecting peer %s: genesis hash mismatch", peer_addr)
                asyncio.create_task(network.disconnect_peer(peer_addr))
                return

            logger.info("🔄 Handshake successful with %s", peer_addr)
            peer_tip = payload.get("latest_block_index", 0)
            if peer_tip > chain.last_block.index:
                logger.info("📡 Peer %s is ahead (%d > %d). Initiating chunked sync...", peer_addr, peer_tip, chain.last_block.index)
                request_chain(network, chain.last_block.index + 1, 500)

        elif msg_type == "tx":
            try:
                tx = Transaction.from_dict(payload)
            except Exception as e:
                logger.warning("Invalid tx payload from %s: %s", peer_addr, e)
                return ValidationStatus.MALFORMED

            if getattr(tx, "chain_id", None) != chain.chain_id:
                logger.warning("Invalid chain_id in tx from %s", peer_addr)
                return ValidationStatus.INVALID

            if mempool.add_transaction(tx):
                logger.info("📥 Received tx from %s... (amount=%s)", tx.sender[:8], tx.amount)
                return ValidationStatus.VALID
            else:
                return ValidationStatus.FAILED

        elif msg_type == "block":
            try:
                block = Block.from_dict(payload)
            except Exception as e:
                logger.warning("Invalid block payload from %s: %s", peer_addr, e)
                return ValidationStatus.MALFORMED

            status = chain.add_block(block)
            if status == ValidationStatus.VALID:
                logger.info("📥 Received Block #%d — added to chain", block.index)
                # Drop only confirmed transactions so higher nonces can remain queued.
                mempool.remove_transactions(block.transactions)
            else:
                if block.index > chain.last_block.index + 1:
                    logger.warning("📥 Received Block #%s — ahead of us (tip: %s). Requesting chunked sync...", block.index, chain.last_block.index)
                    request_chain(network, chain.last_block.index + 1, 500)
                else:
                    logger.warning("📥 Received Block #%s — rejected. Fork detected, trigger reorg sync.", block.index)
                    # For a fork, request the full chain to use resolve_conflicts
                    request_chain(network, 0, 1000000)
            return status

        elif msg_type == "chain_request":
            start_index = payload.get("start_index", 0)
            limit = payload.get("limit", 500)
            logger.info("📡 Peer requested blocks from %d (limit %d).", start_index, limit)
            
            if start_index < len(chain.chain):
                blocks_slice = chain.chain[start_index : start_index + limit]
                blocks_dicts = [b.to_dict() for b in blocks_slice]
            else:
                blocks_dicts = []
                
            resp_payload = {"type": "chain_response", "data": {"blocks": blocks_dicts, "requested_limit": limit}}
            asyncio.create_task(network._unicast_raw(peer_addr, resp_payload))

        elif msg_type == "chain_response":
            blocks_payload = payload.get("blocks", [])
            requested_limit = payload.get("requested_limit", 500)
            if not blocks_payload:
                return

            new_chain = []
            try:
                new_chain = [Block.from_dict(b) for b in blocks_payload]
            except Exception as e:
                logger.warning("❌ Failed to parse chain_response: %s", e)
                return

            if new_chain:
                # Distinguish between linear catch-up vs full reorg based on whether we received block 0
                if new_chain[0].index == 0:
                    # Fork / Reorg sync
                    success, orphans = chain.resolve_conflicts(new_chain)
                    if success:
                        logger.info("🔄 Reorg complete! Restoring %d orphaned txs to mempool.", len(orphans))
                        for tx in orphans:
                            mempool.add_transaction(tx)
                else:
                    # Linear Catch-up
                    all_added = True
                    for block in new_chain:
                        if block.index <= chain.last_block.index:
                            continue # Ignore already known blocks
                        if chain.add_block(block) == ValidationStatus.VALID:
                            logger.info("📥 Synced Block #%d", block.index)
                            mempool.remove_transactions(block.transactions)
                        else:
                            logger.warning("❌ Sync failed at Block #%d. Fork detected. Requesting full chain.", block.index)
                            request_chain(network, 0, 1000000)
                            all_added = False
                            break

                    # If we added all blocks and we hit the limit, request next batch
                    if all_added and len(new_chain) == requested_limit:
                        next_index = chain.last_block.index + 1
                        logger.info("📡 Requesting next batch from index %d", next_index)
                        request_chain(network, next_index, requested_limit)

    return handler


# ──────────────────────────────────────────────
# Interactive CLI
# ──────────────────────────────────────────────

C_CYAN = '\033[96m'
C_BLUE = '\033[94m'
C_YELLOW = '\033[38;2;255;205;0m' # Golden Wallet (#FFCD00)
C_GREEN = '\033[38;2;0;132;61m'    # Baggy Green (#00843D)
C_RED = '\033[91m'
C_RESET = '\033[0m'
C_BOLD = '\033[1m'

def gradient_text(text: str, c1: tuple[int, int, int], c2: tuple[int, int, int]) -> str:
    """Applies a smooth horizontal color gradient to text."""
    lines = text.strip('\n').split('\n')
    out = []
    max_len = max(len(line) for line in lines) if lines else 1
    
    for line in lines:
        line_out = ""
        for i, char in enumerate(line):
            t = i / max(1, max_len - 1)
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            line_out += f"\033[38;2;{r};{g};{b}m{char}"
        out.append(line_out + C_RESET)
    return "\n".join(out)

RAW_LOGO = r"""
███╗   ███╗██╗███╗   ██╗██╗ ██████╗██╗  ██╗ █████╗ ██╗███╗   ██╗
████╗ ████║██║████╗  ██║██║██╔════╝██║  ██║██╔══██╗██║████╗  ██║
██╔████╔██║██║██╔██╗ ██║██║██║     ███████║███████║██║██╔██╗ ██║
██║╚██╔╝██║██║██║╚██╗██║██║██║     ██╔══██║██╔══██║██║██║╚██╗██║
██║ ╚═╝ ██║██║██║ ╚████║██║╚██████╗██║  ██║██║  ██║██║██║ ╚████║
╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝
"""

ASCII_LOGO = gradient_text(RAW_LOGO, (255, 205, 0), (0, 132, 61))

HELP_TEXT = f"""
{C_BOLD}{ASCII_LOGO}{C_RESET}
{C_CYAN}╔══════════════════════════════════════════════════════════════╗{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}balance{C_RESET}                 - show all balances                 {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}send <to> <amount>{C_RESET}      - send coins                        {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}deploy <file>{C_RESET}           - deploy a contract                 {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}call <addr> <data>{C_RESET}      - call a contract                   {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}mine{C_RESET}                    - mine a block                      {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}peers{C_RESET}                   - show connected peers              {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}connect <multiaddr>{C_RESET}     - connect to a peer                 {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}address{C_RESET}                 - show your public key              {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}export_key{C_RESET}              - show your private key             {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}import_key <hex>{C_RESET}        - import a private key              {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}chain{C_RESET}                   - show chain summary                {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}help{C_RESET}                    - show this help                    {C_CYAN}║{C_RESET}
{C_CYAN}║{C_RESET}  {C_GREEN}quit{C_RESET}                    - shut down                         {C_CYAN}║{C_RESET}
{C_CYAN}╚══════════════════════════════════════════════════════════════╝{C_RESET}
"""


async def cli_loop(sk, pk, chain, mempool, network, datadir: str | None = None):
    """Read commands from stdin asynchronously."""
    loop = asyncio.get_event_loop()
    print(HELP_TEXT)
    
    def print_prompt_info(current_pk):
        print(f"  {C_YELLOW}Your address:{C_RESET} {C_BOLD}{current_pk}{C_RESET}\n")
    
    print_prompt_info(pk)

    while True:
        try:
            raw = await loop.run_in_executor(None, lambda: input(f"{C_CYAN}minichain>{C_RESET} "))
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
                tag = f" {C_GREEN}(you){C_RESET}" if addr == pk else ""
                contract_tag = f" {C_CYAN}[Contract]{C_RESET}" if acc.get("code") else ""
                print(f"  {C_BOLD}{addr[:12]}...{C_RESET}  balance={C_YELLOW}{acc['balance']}{C_RESET}  nonce={acc['nonce']}{tag}{contract_tag}")

        # ── send ──
        elif cmd == "send":
            if len(parts) < 3:
                print("  Usage: send <receiver_address> <amount> [gas_limit] [max_fee_per_gas]")
                continue
            receiver = parts[1]
            if not is_valid_receiver(receiver):
                print("  Invalid receiver format. Expected 40 or 64 hex characters.")
                continue
            try:
                amount = int(parts[2])
                gas_limit = int(parts[3]) if len(parts) > 3 else 0
                max_fee = int(parts[4]) if len(parts) > 4 else 0
            except ValueError:
                print("  Values must be integers.")
                continue
            if amount <= 0:
                print("  Amount must be greater than 0.")
                continue
            if gas_limit < 0 or max_fee < 0:
                print("  Gas limit and max fee cannot be negative.")
                continue

            nonce = chain.state.get_account(pk).get("nonce", 0)
            tx = Transaction(sender=pk, receiver=receiver, amount=amount, nonce=nonce, gas_limit=gas_limit, max_fee_per_gas=max_fee, chain_id=chain.chain_id)
            tx.sign(sk)

            await submit_and_broadcast(
                mempool, network, tx,
                f"  {C_GREEN}✅ Tx sent:{C_RESET} {amount} coins → {receiver[:12]}...",
                f"  {C_RED}❌ Transaction rejected{C_RESET} (invalid sig, duplicate, or mempool full).",
            )

        # ── deploy ──
        elif cmd == "deploy":
            if len(parts) < 2:
                print("  Usage: deploy <filepath> [amount] [gas_limit] [max_fee_per_gas]")
                continue
            filepath = parts[1]
            try:
                with open(filepath, "r") as f:
                    code = f.read()
            except FileNotFoundError:
                print(f"  File not found: {filepath}")
                continue
            
            parsed = parse_tx_params(parts, 2)
            if parsed is None:
                continue
            amount, gas_limit, max_fee = parsed

            nonce = chain.state.get_account(pk).get("nonce", 0)
            tx = Transaction(sender=pk, receiver=None, amount=amount, nonce=nonce, gas_limit=gas_limit, max_fee_per_gas=max_fee, data=code, chain_id=chain.chain_id)
            tx.sign(sk)

            await submit_and_broadcast(
                mempool, network, tx,
                f"  ✅ Deploy Tx sent (nonce={nonce}). Mine a block to confirm.",
                "  ❌ Deploy Transaction rejected.",
            )

        # ── call ──
        elif cmd == "call":
            if len(parts) < 3:
                print("  Usage: call <contract_address> <payload> [amount] [gas_limit] [max_fee_per_gas]")
                continue
            receiver = parts[1]
            if not is_valid_receiver(receiver):
                print("  Invalid receiver format. Expected 40 or 64 hex characters.")
                continue
            payload = parts[2]

            parsed = parse_tx_params(parts, 3)
            if parsed is None:
                continue
            amount, gas_limit, max_fee = parsed

            nonce = chain.state.get_account(pk).get("nonce", 0)
            tx = Transaction(sender=pk, receiver=receiver, amount=amount, nonce=nonce, gas_limit=gas_limit, max_fee_per_gas=max_fee, data=payload, chain_id=chain.chain_id)
            tx.sign(sk)

            await submit_and_broadcast(
                mempool, network, tx,
                f"  ✅ Call Tx sent to {receiver[:12]}... (payload='{payload}'). Mine a block to confirm.",
                "  ❌ Call Transaction rejected.",
            )

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
                print("  Usage: connect <multiaddress>")
                continue
            maddr_str = parts[1]
            success = await network.connect_to_peer(maddr_str)
            if success:
                print(f"  Attempting to dial {maddr_str}...")
            else:
                print(f"  Failed to initiate connection to {maddr_str}")

        # ── address ──
        elif cmd == "address":
            print(f"  {pk}")

        # ── export_key ──
        elif cmd == "export_key":
            sk_hex = sk.encode(encoder=HexEncoder).decode()
            print(f"  {C_YELLOW}Private Key:{C_RESET} {sk_hex}")
            print(f"  {C_RED}Warning: Never share this key!{C_RESET}")

        # ── import_key ──
        elif cmd == "import_key":
            if len(parts) < 2:
                print("  Usage: import_key <hex_private_key>")
                continue
            
            try:
                new_sk = SigningKey(bytes.fromhex(parts[1]))
                new_pk = new_sk.verify_key.encode(encoder=HexEncoder).decode()
                
                path = datadir or "."
                keystore_path = os.path.join(path, "keystore.json")
                os.makedirs(path, exist_ok=True)
                with open(keystore_path, "w") as f:
                    json.dump({"private_key": new_sk.encode(encoder=HexEncoder).decode()}, f)
                
                sk, pk = new_sk, new_pk
                print(f"  {C_GREEN}✅ Key imported successfully!{C_RESET}")
                print_prompt_info(pk)
            except Exception as e:
                print(f"  {C_RED}❌ Failed to import key: {e}{C_RESET}")

        # ── chain ──
        elif cmd == "chain":
            print(f"  Chain length: {len(chain.chain)} blocks")
            for b in chain.chain:
                tx_count = len(b.transactions) if b.transactions else 0
                print(f"    Block #{b.index}  hash={b.hash[:16]}...  txs={tx_count}")

        # ── list-banned ──
        elif cmd == "list-banned":
            from minichain.persistence import get_banned_peers
            banned = get_banned_peers(path=datadir or ".")
            if not banned:
                print("  No peers are currently banned.")
            else:
                print(f"  {len(banned)} banned peer(s):")
                for p in banned:
                    print(f"    - {p['peer_id']} (Reason: {p['reason']}, Time: {p['timestamp']})")

        # ── ban ──
        elif cmd == "ban":
            if len(parts) < 2:
                print("  Usage: ban <peer_id>")
                continue
            peer_id = parts[1]
            from minichain.persistence import ban_peer
            ban_peer(peer_id, reason="Manual ban via CLI", path=datadir or ".")
            await network.disconnect_peer(f"peer:{peer_id}")
            print(f"  ✅ Peer {peer_id} banned.")

        # ── unban ──
        elif cmd == "unban":
            if len(parts) < 2:
                print("  Usage: unban <peer_id>")
                continue
            peer_id = parts[1]
            from minichain.persistence import unban_peer
            unban_peer(peer_id, path=datadir or ".")
            print(f"  ✅ Peer {peer_id} unbanned.")

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
    sk, pk = load_or_create_wallet(datadir)

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
    network = P2PNetwork(data_path=datadir or ".")

    handler = make_network_handler(chain, mempool, network)
    network.register_handler(handler)
    
    rpc_server = JSONRPCServer(chain, mempool, network)

    # When a new peer connects, send our hello so they can handshake
    async def on_peer_connected(writer):
        sync_msg = json.dumps({
            "type": "hello",
            "data": {
                "chain_id": chain.chain_id,
                "genesis_hash": chain.chain[0].hash,
                "latest_block_index": chain.last_block.index,
                "latest_block_hash": chain.last_block.hash
            }
        }) + "\n"
        writer.write(sync_msg.encode())
        await writer.drain()
        logger.info("🔄 Sent hello handshake to new peer")

    network.register_on_peer_connected(on_peer_connected)

    await network.start(port=port, host=host)
    
    # Start RPC server on a port correlated to the node port (e.g. 8545 if P2P is 9000)
    rpc_port = 8545 + (port - 9000)
    await rpc_server.start(host="127.0.0.1", port=rpc_port)

    # Fund this node's wallet so it can transact in the demo
    if fund > 0:
        chain.state.credit_mining_reward(pk, reward=fund)
        logger.info("💰 Funded %s... with %d coins", pk[:12], fund)

    # Connect to a seed peer if requested
    if connect_to:
        await network.connect_to_peer(connect_to)

    try:
        await cli_loop(sk, pk, chain, mempool, network, datadir)
    finally:
        # Save chain to disk on shutdown
        if datadir:
            try:
                from minichain.persistence import save
                save(chain, datadir)
                logger.info("Chain saved to '%s'", datadir)
            except Exception as e:
                logger.error("Failed to save chain during shutdown: %s", e)
        
        await rpc_server.stop()
        await network.stop()


def main():
    parser = argparse.ArgumentParser(description="MiniChain Node — Testnet Demo")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host/IP to bind the P2P server (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="TCP port to listen on (default: 9000)")
    parser.add_argument("--connect", type=str, default=None, help="Peer address to connect to (multiaddr)")
    parser.add_argument("--fund", type=int, default=100, help="Initial coins to fund this wallet (default: 100)")
    parser.add_argument("--datadir", type=str, default=".minichain", help="Directory to save/load blockchain state (enables persistence)")
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
