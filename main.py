import asyncio
import logging
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, Blockchain, Block, State, Mempool, P2PNetwork, mine_block
from minichain.mining import mine_and_process_block, sync_nonce


logger = logging.getLogger(__name__)


def create_wallet():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


async def node_loop():
    logger.info("Starting MiniChain Node with Smart Contracts")

    chain = Blockchain()
    mempool = Mempool()

    pending_nonce_map = {}

    def claim_nonce(address) -> int:
        account = chain.state.get_account(address)
        account_nonce = account.get("nonce", 0) if account else 0
        local_nonce = pending_nonce_map.get(address, account_nonce)
        next_nonce = max(account_nonce, local_nonce)
        pending_nonce_map[address] = next_nonce + 1
        return next_nonce

    network = P2PNetwork()

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

    network.register_handler(_handle_network_data)

    try:
        await _run_node(network, chain, mempool, pending_nonce_map, claim_nonce)
    finally:
        await network.stop()


async def _run_node(network, chain, mempool, pending_nonce_map, get_next_nonce):
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
