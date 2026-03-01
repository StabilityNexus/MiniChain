import asyncio
import logging
import re
import time
from nacl.signing import SigningKey
import nacl.encoding

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

    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=pending_txs,
    )

    # PID Difficulty Adjustment (handled internally)
    block.difficulty = chain.difficulty_adjuster.adjust(
        chain.last_block.difficulty
    )

    start_time = time.time()
    mined_block = mine_block(block)
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

    else:
        logger.error("Block rejected by chain")
        return None, []


# -------------------------
# Nonce Sync
# -------------------------
def sync_nonce(state, pending_nonce_map, address):
    account = state.get_account(address)
    pending_nonce_map[address] = account.get("nonce", 0) if account else 0


# -------------------------
# Node Logic
# -------------------------
async def node_loop():
    logger.info("Starting MiniChain Node with PID Difficulty Adjustment")

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

                chain.add_block(block)

        except Exception as e:
            logger.error(f"Network error: {e}")

    network.register_handler(_handle_network_data)

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
    bob_sk, bob_pk = create_wallet()

    # Initial funding
    chain.state.credit_mining_reward(alice_pk, reward=100)
    sync_nonce(chain.state, pending_nonce_map, alice_pk)

    # Alice sends Bob 10 coins
    logger.info("[2] Alice sending 10 coins to Bob")

    tx_payment = Transaction(
        sender=alice_pk,
        receiver=bob_pk,
        amount=10,
        nonce=get_next_nonce(alice_pk),
    )
    tx_payment.sign(alice_sk)

    if mempool.add_transaction(tx_payment):
        await network.broadcast_transaction(tx_payment)

    # -------------------------------
    # PID Demo: Mining 5 Blocks
    # -------------------------------
    logger.info("[3] Mining Multiple Blocks (Watch Difficulty Adjust)")

    for i in range(5):
        await asyncio.sleep(1)

        logger.info(f"\nMining Block {i+1}")

        mined_block, _ = mine_and_process_block(
            chain, mempool, pending_nonce_map
        )

        if mined_block:
            logger.info("Block mined in %.2f seconds",
                        mined_block.mining_time)

            logger.info("New difficulty: %s",
                        chain.last_block.difficulty)

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
def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )

    try:
        asyncio.run(node_loop())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()