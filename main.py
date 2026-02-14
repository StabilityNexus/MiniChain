import asyncio
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from core import Transaction, Blockchain, Block, State
from node import Mempool
from network import P2PNetwork
from consensus import mine_block


def create_wallet():
    """Generate a new keypair."""
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


async def node_loop():
    print("--- Starting MiniChain Node with Smart Contracts ---")

    # Initialize core components
    state = State()
    chain = Blockchain()
    mempool = Mempool()

    # Track pending nonces locally
    pending_nonce_map = {}

    def get_next_nonce(address):
        if address not in pending_nonce_map:
            pending_nonce_map[address] = state.get_account(address)['nonce']
        return pending_nonce_map[address]

    def increment_nonce(address):
        pending_nonce_map[address] += 1

    async def handle_network_data(data):
        print(f"[Network] Received: {data}")

    network = P2PNetwork(handle_network_data)
    await network.start()

    # Create wallets
    alice_sk, alice_pk = create_wallet()
    bob_sk, bob_pk = create_wallet()

    print(f"Alice Address: {alice_pk[:10]}...")
    print(f"Bob Address:   {bob_pk[:10]}...")

    # Credit Alice with initial balance
    print("\n[1] Genesis: Crediting Alice with 100 coins")
    state.credit_mining_reward(alice_pk, reward=100)

    # Alice sends 10 coins to Bob
    print("\n[2] Transaction: Alice sends 10 coins to Bob")

    nonce = get_next_nonce(alice_pk)

    tx_payment = Transaction(
        sender=alice_pk,
        receiver=bob_pk,
        amount=10,
        nonce=nonce
    )
    tx_payment.sign(alice_sk)
    increment_nonce(alice_pk)

    if mempool.add_transaction(tx_payment):
        await network.broadcast_transaction(tx_payment)

    # Alice deploys a storage contract
    print("\n[3] Smart Contract: Alice deploys a 'Storage' contract")

    contract_code = """
# Storage Contract
if msg['data']:
    storage['value'] = msg['data']
    print(f"Contract: Stored value '{msg['data']}'")
"""

    nonce = get_next_nonce(alice_pk)

    tx_deploy = Transaction(
        sender=alice_pk,
        receiver=None,
        amount=0,
        nonce=nonce,
        data=contract_code
    )
    tx_deploy.sign(alice_sk)
    increment_nonce(alice_pk)

    if mempool.add_transaction(tx_deploy):
        await network.broadcast_transaction(tx_deploy)

    # Mine block 1
    print("\n[4] Consensus: Mining Block 1...")

    pending_txs = mempool.get_transactions_for_block()

    block_1 = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=pending_txs
    )

    mined_block_1 = mine_block(block_1)

    contract_address = None

    if chain.add_block(mined_block_1):
        print(f"    Block #{mined_block_1.index} added!")

        for tx in mined_block_1.transactions:
            result = state.apply_transaction(tx)

            if isinstance(result, str):
                contract_address = result
                print(f"    -> New Contract Deployed at: {contract_address[:10]}...")

    # Bob interacts with deployed contract
    print("\n[5] Interaction: Bob sends data 'Hello Blockchain' to Contract")

    if contract_address is None:
        print("ERROR: Contract not deployed. Skipping interaction.")
        return

    nonce = get_next_nonce(bob_pk)

    tx_call = Transaction(
        sender=bob_pk,
        receiver=contract_address,
        amount=0,
        nonce=nonce,
        data="Hello Blockchain"
    )
    tx_call.sign(bob_sk)
    increment_nonce(bob_pk)

    mempool.add_transaction(tx_call)

    # Mine block 2
    print("\n[6] Consensus: Mining Block 2...")

    pending_txs_2 = mempool.get_transactions_for_block()

    block_2 = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=pending_txs_2
    )

    mined_block_2 = mine_block(block_2)

    if chain.add_block(mined_block_2):
        print(f"    Block #{mined_block_2.index} added!")
        for tx in mined_block_2.transactions:
            state.apply_transaction(tx)
    else:
        print("ERROR: Block 2 rejected by chain!")

    # Final balances and contract state
    print("\n[7] Final State Check:")
    print(f"    Alice Balance: {state.get_account(alice_pk)['balance']}")
    print(f"    Bob Balance:   {state.get_account(bob_pk)['balance']}")

    if contract_address is not None:
        contract_acc = state.get_account(contract_address)
        print(f"    Contract Storage: {contract_acc['storage']}")
    else:
        print("    No contract deployed.")


if __name__ == "__main__":
    asyncio.run(node_loop())
