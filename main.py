import asyncio
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

# Imports from flat directory structure
from core import Transaction, Blockchain, Block, State
from node import Mempool
from network import P2PNetwork
from consensus import mine_block

def create_wallet():
    """Generates a new keypair for a user."""
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk

async def node_loop():
    print("--- Starting MiniChain Node with Smart Contracts ---")
    
    # 1. Initialize Components
    state = State()
    chain = Blockchain()
    mempool = Mempool()
    
    # Mock network handler
    async def handle_network_data(data):
        print(f"[Network] Received: {data}")
    
    network = P2PNetwork(handle_network_data)
    await network.start()

    # 2. Setup Wallets
    alice_sk, alice_pk = create_wallet()
    bob_sk, bob_pk = create_wallet()
    
    print(f"Alice Address: {alice_pk[:10]}...")
    print(f"Bob Address:   {bob_pk[:10]}...")

    # 3. Genesis: Credit Alice with funds
    print("\n[1] Genesis: Crediting Alice with 100 coins")
    state.credit_mining_reward(alice_pk, reward=100)

    # ------------------------------------------------------------------
    # SCENARIO A: Standard Transaction (Alice -> Bob)
    # ------------------------------------------------------------------
    print("\n[2] Transaction: Alice sends 10 coins to Bob")
    nonce = state.get_account(alice_pk)['nonce']
    tx_payment = Transaction(
        sender=alice_pk,
        receiver=bob_pk,
        amount=10,
        nonce=nonce
    )
    tx_payment.sign(alice_sk)

    if mempool.add_transaction(tx_payment):
        await network.broadcast_transaction(tx_payment)

    # ------------------------------------------------------------------
    # SCENARIO B: Deploy Smart Contract (Alice deploys a 'Storage' contract)
    # ------------------------------------------------------------------
    print("\n[3] Smart Contract: Alice deploys a 'Storage' contract")
    
    # Simple Python code that stores a value sent in 'msg'
    contract_code = """
# Storage Contract
# If data is sent, store it.
if msg['data']:
    storage['value'] = msg['data']
    print(f"Contract: Stored value '{msg['data']}'")
"""
    
    nonce = state.get_account(alice_pk)['nonce'] + 1 # Increment manually since block not mined yet
    
    # Receiver is None (or empty string) to indicate deployment
    tx_deploy = Transaction(
        sender=alice_pk,
        receiver=None, 
        amount=0,
        nonce=nonce,
        data=contract_code
    )
    tx_deploy.sign(alice_sk)

    if mempool.add_transaction(tx_deploy):
        await network.broadcast_transaction(tx_deploy)

    # ------------------------------------------------------------------
    # MINING BLOCK 1 (Process Payment & Deployment)
    # ------------------------------------------------------------------
    print("\n[4] Consensus: Mining Block 1...")
    pending_txs = mempool.get_transactions_for_block()
    
    block_1 = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=pending_txs
    )
    
    mined_block_1 = mine_block(block_1)
    
    if chain.add_block(mined_block_1):
        print(f"    Block #{mined_block_1.index} added!")
        for tx in mined_block_1.transactions:
            result = state.apply_transaction(tx)
            # If a contract was created, we want to know its address
            if result and isinstance(result, str): 
                contract_address = result
                print(f"    -> New Contract Deployed at: {contract_address[:10]}...")

    # ------------------------------------------------------------------
    # SCENARIO C: Interact with Contract (Bob calls the contract)
    # ------------------------------------------------------------------
    print(f"\n[5] Interaction: Bob sends data 'Hello Blockchain' to Contract")
    
    nonce = state.get_account(bob_pk)['nonce']
    
    tx_call = Transaction(
        sender=bob_pk,
        receiver=contract_address, # The address we got from deployment
        amount=0,
        nonce=nonce,
        data="Hello Blockchain"
    )
    tx_call.sign(bob_sk)
    
    mempool.add_transaction(tx_call)

    # ------------------------------------------------------------------
    # MINING BLOCK 2
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # FINAL CHECKS
    # ------------------------------------------------------------------
    print(f"\n[7] Final State Check:")
    print(f"    Alice Balance: {state.get_account(alice_pk)['balance']}")
    print(f"    Bob Balance:   {state.get_account(bob_pk)['balance']}")
    
    contract_acc = state.get_account(contract_address)
    print(f"    Contract Storage: {contract_acc['storage']}")

if __name__ == "__main__":
    asyncio.run(node_loop())