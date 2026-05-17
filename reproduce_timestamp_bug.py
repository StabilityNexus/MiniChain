from minichain.block import Block
from minichain.chain import Blockchain

print("\nInitializing Blockchain...")

blockchain = Blockchain()

genesis = blockchain.last_block

print("\nGenesis Block Created")
print("Genesis Index:", genesis.index)
print("Genesis Timestamp:", genesis.timestamp)
print("Genesis Hash:", genesis.hash)

print("\n--- Current Blockchain ---")
for block in blockchain.chain:
    print(f"Index: {block.index}, Timestamp: {block.timestamp}, Hash: {block.hash}")
print("--------------------------")

# -------------------------
# PAST TIMESTAMP BLOCK
# -------------------------

print("\nCreating malicious block with PAST timestamp...")

past_block = Block(index=1, previous_hash=genesis.hash, transactions=[], timestamp=0)

past_block.hash = past_block.compute_hash()

result1 = blockchain.add_block(past_block)

print("\nPast Block Added:", result1)
print("Past Block Timestamp:", past_block.timestamp)

print("\n--- Current Blockchain ---")
for block in blockchain.chain:
    print(f"Index: {block.index}, Timestamp: {block.timestamp}, Hash: {block.hash}")
print("--------------------------")

# -------------------------
# FUTURE TIMESTAMP BLOCK
# -------------------------

print("\nCreating malicious block with FUTURE timestamp...")

future_block = Block(
    index=2, previous_hash=past_block.hash, transactions=[], timestamp=9999999999999
)

future_block.hash = future_block.compute_hash()

result2 = blockchain.add_block(future_block)

print("\nFuture Block Added:", result2)
print("Future Block Timestamp:", future_block.timestamp)

print("\n--- Current Blockchain ---")
for block in blockchain.chain:
    print(f"Index: {block.index}, Timestamp: {block.timestamp}, Hash: {block.hash}")
print("--------------------------")

print("\nFinal Blockchain Length:", len(blockchain.chain))

if result1 and result2:
    print("\nVULNERABILITY CONFIRMED")
    print("Blockchain accepts miner-controlled timestamps")
    print("No timestamp validation in chain.py")
