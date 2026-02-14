from core.block import Block
from consensus.pow import mine_block

class Blockchain:
    def __init__(self):
        self.chain = []
        self.create_genesis_block()

    def create_genesis_block(self):
        genesis_block = Block(0, "0", [])
        # Mine genesis with lower difficulty for speed startup
        mined_genesis = mine_block(genesis_block, difficulty=2)
        self.chain.append(mined_genesis)

    @property
    def last_block(self):
        return self.chain[-1]

    def add_block(self, block):
        # 1. Check Linkage
        if block.previous_hash != self.last_block.hash:
            print("Error: Invalid Previous Hash")
            return False
        
        # 2. Check PoW (Simple check for demonstration)
        if not block.hash.startswith('0000'): 
            print("Error: Invalid Proof of Work")
            return False
        
        self.chain.append(block)
        return True