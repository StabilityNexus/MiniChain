from consensus.pow import calculate_hash, mine_block
from core.block import Block


class Blockchain:
    def __init__(self, state, difficulty=4):
        self.chain = []
        self.state = state
        self.difficulty = difficulty
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

        # 2. Recompute Hash (DO NOT trust provided hash)
        computed_hash = calculate_hash(block.to_header_dict())

        if computed_hash != block.hash:
            print("Error: Block hash mismatch")
            return False

        # 3. Enforce Correct Difficulty
        required_difficulty = block.difficulty or self.difficulty

        if not computed_hash.startswith("0" * required_difficulty):
            print("Error: Invalid Proof of Work")
            return False

        # 4. Verify and Apply Transactions Safely
        temp_state = self.state.copy()

        for tx in block.transactions:

            if not tx.verify_signature():
                print("Error: Invalid Transaction Signature")
                return False

            if not temp_state.validate_and_apply(tx):
                print("Error: Invalid State Transition")
                return False

        # 5. Commit Block + State
        self.state = temp_state
        self.chain.append(block)

        return True
