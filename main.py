import asyncio
import logging
import re
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from core import Transaction, Blockchain, Block, State
from node import Mempool
from network import P2PNetwork
from consensus import mine_block


logger = logging.getLogger(__name__)

BURN_ADDRESS = "0" * 40


def create_wallet():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


def mine_and_process_block(chain, mempool, pending_nonce_map):
    """
    Mine block and let Blockchain handle validation + state updates.
    DO NOT manually apply transactions again.
    """

    pending_txs = mempool.get_transactions_for_block()

    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=pending_txs,
    )

    mined_block = mine_block(block)

    if not hasattr(mined_block, "miner"):
        mined_block.miner = BURN_ADDRESS

    deployed_contracts: list[str] = []

    if chain.add_block(mined_block):
        logger.info("Block #%s added", mined_block.index)

        miner_attr = getattr(mined_block, "miner", None)
        if isinstance(miner_attr, str) and re.match(r'^[0-9a-fA-F]{40}$', miner_attr):
            miner_address = miner_attr
        else:
            logger.warning("Invalid miner address. Crediting burn address.")
            miner_address = BURN_ADDRESS

        # Reward must go through chain.state
        chain.state.credit_mining_reward(miner_address)

        for tx in mined_block.transactions:
            sync_nonce(chain.state, pending_nonce_map, tx.sender)

            # Track deployed contracts if your state.apply_transaction returns address
            result = chain.state.get_account(tx.receiver) if tx.receiver else None
            if isinstance(result, dict):
                deployed_contracts.append(tx.receiver)

        return mined_block, deployed_contracts
    else:
        logger.error("Block rejected by chain")
        return None, []


def sync_nonce(state, pending_nonce_map, address):
    account = state.get_account(address)
    if account and "nonce" in account:
        pending_nonce_map[address] = account["nonce"]
    else:
        pending_nonce_map[address] = 0


async def node_loop():
    logger.info("Starting MiniChain Node with Smart Contracts")

    state = State()
    chain = Blockchain(state)
    mempool = Mempool()

    pending_nonce_map = {}

    def claim_nonce(address):
        account = state.get_account(address)
        account_nonce = account.get("nonce", 0) if account else 0
        local_nonce = pending_nonce_map.get(address, account_nonce)
        next_nonce = max(account_nonce, local_nonce)
        pending_nonce_map[address] = next_nonce + 1
        return next_nonce

    network = P2PNetwork(None)

    async def _handle_network_data(data):
        logger.info("Received network data: %s", data)

        try:
            if data["type"] == "tx":
                tx = Transaction(**data["data"])
                if mempool.add_transaction(tx):
                    await network.broadcast_transaction(tx)

            elif data["type"] == "block":
                block_data = data["data"]
                transactions_raw = block_data.get("transactions", [])
                transactions = [Transaction(**tx_data) for tx_data in transactions_raw]

                block = Block(
                    index=block_data.get("index"),
                    previous_hash=block_data.get("previous_hash"),
                    transactions=transactions,
                    timestamp=block_data.get("timestamp"),
                    difficulty=block_data.get("difficulty")
                )

                block.nonce = block_data.get("nonce", 0)
                block.hash = block_data.get("hash")

                if chain.add_block(block):
                    logger.info("Received block added to chain: #%s", block.index)

        except Exception:
            logger.exception("Error processing network data: %s", data)

    network.handler_callback = _handle_network_data

    try:
        await _run_node(network, state, chain, mempool, pending_nonce_map, claim_nonce)
    finally:
        await network.stop()


async def _run_node(network, state, chain, mempool, pending_nonce_map, get_next_nonce):
    await network.start()

    alice_sk, alice_pk = create_wallet()
    bob_sk, bob_pk = create_wallet()

    logger.info("Alice Address: %s...", alice_pk[:10])
    logger.info("Bob Address: %s...", bob_pk[:10])

    logger.info("[1] Genesis: Crediting Alice with 100 coins")
    chain.state.credit_mining_reward(alice_pk, reward=100)
    sync_nonce(chain.state, pending_nonce_map, alice_pk)

    # -------------------------------
    # Alice Payment
    # -------------------------------

    logger.info("[2] Transaction: Alice sends 10 coins to Bob")

    nonce = get_next_nonce(alice_pk)

    tx_payment = Transaction(
        sender=alice_pk,
        receiver=bob_pk,
        amount=10,
        nonce=nonce,
    )
    tx_payment.sign(alice_sk)

    if mempool.add_transaction(tx_payment):
        await network.broadcast_transaction(tx_payment)

    # -------------------------------
    # Mine Block 1
    # -------------------------------

    logger.info("[3] Mining Block 1")
    mine_and_process_block(chain, mempool, pending_nonce_map)

    # -------------------------------
    # Final State Check
    # -------------------------------

    logger.info("[4] Final State Check")

    alice_acc = chain.state.get_account(alice_pk)
    bob_acc = chain.state.get_account(bob_pk)

    logger.info("Alice Balance: %s", alice_acc.get("balance", 0) if alice_acc else 0)
    logger.info("Bob Balance: %s", bob_acc.get("balance", 0) if bob_acc else 0)


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(node_loop())


if __name__ == "__main__":
    main()
