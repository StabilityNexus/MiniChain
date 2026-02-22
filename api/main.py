from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import hashlib
import json

from core import Blockchain, Block, State, Transaction
from core.merkle import MerkleTree

app = FastAPI(title="MiniChain API", description="SPV-enabled blockchain API")

blockchain = Blockchain()


class TransactionResponse(BaseModel):
    sender: str
    receiver: str
    amount: int
    nonce: int
    data: Optional[dict] = None
    timestamp: int
    signature: Optional[str] = None
    hash: Optional[str] = None


class BlockResponse(BaseModel):
    index: int
    previous_hash: str
    merkle_root: Optional[str]
    timestamp: int
    difficulty: Optional[int]
    nonce: int
    hash: str
    transactions: List[TransactionResponse]
    merkle_proofs: Optional[dict] = None


class VerifyTransactionResponse(BaseModel):
    tx_hash: str
    block_index: int
    merkle_root: str
    proof: List[dict]
    verification_status: bool
    message: str


class ChainInfo(BaseModel):
    length: int
    blocks: List[dict]


def compute_tx_hash(tx_dict: dict) -> str:
    return hashlib.sha256(json.dumps(tx_dict, sort_keys=True).encode()).hexdigest()


@app.get("/")
def root():
    return {"message": "MiniChain API with SPV Support"}


@app.get("/chain", response_model=ChainInfo)
def get_chain():
    return {
        "length": len(blockchain.chain),
        "blocks": [block.to_dict() for block in blockchain.chain]
    }


@app.get("/block/{block_index}", response_model=BlockResponse)
def get_block(block_index: int):
    if block_index < 0 or block_index >= len(blockchain.chain):
        raise HTTPException(status_code=404, detail="Block not found")
    
    block = blockchain.chain[block_index]
    block_dict = block.to_dict()
    
    merkle_proofs = {}
    for i, tx in enumerate(block.transactions):
        tx_hash = compute_tx_hash(tx.to_dict())
        proof = block.get_merkle_proof(i)
        if proof:
            merkle_proofs[tx_hash] = proof
    
    return {
        **block_dict,
        "merkle_proofs": merkle_proofs
    }


@app.get("/verify_transaction", response_model=VerifyTransactionResponse)
def verify_transaction(
    tx_hash: str = Query(..., description="Transaction hash to verify"),
    block_index: int = Query(..., description="Block index to verify against")
):
    if block_index < 0 or block_index >= len(blockchain.chain):
        raise HTTPException(status_code=404, detail="Block not found")
    
    block = blockchain.chain[block_index]
    
    tx_found = False
    tx_index = -1
    for i, tx in enumerate(block.transactions):
        tx_hash_computed = compute_tx_hash(tx.to_dict())
        if tx_hash_computed == tx_hash:
            tx_found = True
            tx_index = i
            break
    
    if not tx_found:
        return {
            "tx_hash": tx_hash,
            "block_index": block_index,
            "merkle_root": block.merkle_root or "",
            "proof": [],
            "verification_status": False,
            "message": "Transaction not found in block"
        }
    
    proof = block.get_merkle_proof(tx_index)
    merkle_root = block.merkle_root or ""
    
    verification_status = MerkleTree.verify_proof(tx_hash, proof, merkle_root)
    
    return {
        "tx_hash": tx_hash,
        "block_index": block_index,
        "merkle_root": merkle_root,
        "proof": proof,
        "verification_status": verification_status,
        "message": "Transaction verified successfully" if verification_status else "Verification failed"
    }


@app.post("/mine")
def mine_block_endpoint():
    from main import mine_and_process_block
    from node import Mempool
    
    mempool = Mempool()
    pending_nonce_map = {}
    
    result = mine_and_process_block(blockchain, mempool, pending_nonce_map)
    
    if result[0]:
        return {"message": "Block mined successfully", "block": result[0].to_dict()}
    else:
        raise HTTPException(status_code=400, detail="Failed to mine block")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
