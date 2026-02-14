from core.block import Block
from consensus.pow import mine_block


class Blockchain:
    def __init__(self, state):
        self.chain = []
        self.state = state
        self.create_genesis_block()

    def create_genesis_block(self):
        genesis_block = Block(0, "0", [], difficulty=2)
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

        # 2. Check PoW
        if not block.hash.startswith("0" * block.difficulty):
            print("Error: Invalid Proof of Work")
            return False

        # 3. Verify and Apply Transactions Safely
        temp_state = self.state.copy()

        for tx in block.transactions:

            # 3.1 Verify Signature
            if not tx.verify_signature():
                print("Error: Invalid Transaction Signature")
                return False

            # 3.2 Validate + Apply State Transition
            if not temp_state.validate_and_apply(tx):
                print("Error: Invalid State Transition")
                return False

        # 4. All transactions valid → Commit state
        self.state = temp_state
        self.chain.append(block)

        return True
