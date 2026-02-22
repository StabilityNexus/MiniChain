from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional, Union, Dict

from core import Blockchain, Block, State, Transaction
from core.merkle import MerkleTree
from node import Mempool
from core.mining import mine_and_process_block


blockchain: Optional[Blockchain] = None
mempool: Optional[Mempool] = None
pending_nonce_map: Dict[str, int] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global blockchain, mempool
    blockchain = Blockchain()
    mempool = Mempool()
    yield
    blockchain.save_to_file()


app = FastAPI(title="MiniChain API", description="SPV-enabled blockchain API", lifespan=lifespan)


class TransactionResponse(BaseModel):
    sender: str
    receiver: Optional[str] = None
    amount: int
    nonce: int
    data: Optional[Union[dict, str]] = None
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
    hash: Optional[str] = None
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


@app.get("/")
def root():
    return {"message": "MiniChain API with SPV Support"}


@app.get("/chain", response_model=ChainInfo)
def get_chain():
    chain_copy = blockchain.get_chain_copy()
    
    return {
        "length": len(chain_copy),
        "blocks": [block.to_dict() for block in chain_copy]
    }


@app.get("/block/{block_index}", response_model=BlockResponse)
def get_block(block_index: int):
    chain_copy = blockchain.get_chain_copy()
    
    if block_index < 0 or block_index >= len(chain_copy):
        raise HTTPException(status_code=404, detail="Block not found")
    
    block = chain_copy[block_index]
    
    block_dict = block.to_dict()
    
    merkle_proofs = {}
    for i, _ in enumerate(block.transactions):
        tx_hash = block.get_tx_hash(i)
        if tx_hash:
            proof = block.get_merkle_proof(i)
            if proof is not None:
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
    chain_copy = blockchain.get_chain_copy()
    
    if block_index < 0 or block_index >= len(chain_copy):
        raise HTTPException(status_code=404, detail="Block not found")
    
    block = chain_copy[block_index]
    
    tx_found = False
    tx_index = -1
    for i, _ in enumerate(block.transactions):
        tx_hash_computed = block.get_tx_hash(i)
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
    
    if proof is None:
        return {
            "tx_hash": tx_hash,
            "block_index": block_index,
            "merkle_root": merkle_root,
            "proof": [],
            "verification_status": False,
            "message": "Failed to generate Merkle proof"
        }
    
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
    global pending_nonce_map
    
    block, *_ = mine_and_process_block(blockchain, mempool, pending_nonce_map)
    
    if block:
        return {"message": "Block mined successfully", "block": block.to_dict()}
    else:
        raise HTTPException(status_code=400, detail="Failed to mine block")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
