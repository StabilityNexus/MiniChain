import unittest
from core.merkle import MerkleTree, calculate_merkle_root


class TestMerkleTree(unittest.TestCase):
    def test_empty_transactions(self):
        root = calculate_merkle_root([])
        self.assertIsNone(root)

    def test_single_transaction(self):
        tx = {"sender": "alice", "receiver": "bob", "amount": 10}
        tree = MerkleTree([tx])
        root = tree.get_merkle_root()
        self.assertIsNotNone(root)
        self.assertEqual(len(root), 64)
        
        proof = tree.get_proof(0)
        self.assertEqual(proof, [])
        
        tx_hash = tree.tx_hashes[0]
        result = MerkleTree.verify_proof(tx_hash, proof, root)
        self.assertTrue(result)

    def test_two_transactions(self):
        txs = [
            {"sender": "alice", "receiver": "bob", "amount": 10},
            {"sender": "bob", "receiver": "charlie", "amount": 5}
        ]
        tree = MerkleTree(txs)
        root = tree.get_merkle_root()
        self.assertIsNotNone(root)
        
        proof0 = tree.get_proof(0)
        proof1 = tree.get_proof(1)
        self.assertIsNotNone(proof0)
        self.assertIsNotNone(proof1)
        
        result0 = MerkleTree.verify_proof(tree.tx_hashes[0], proof0, root)
        result1 = MerkleTree.verify_proof(tree.tx_hashes[1], proof1, root)
        self.assertTrue(result0)
        self.assertTrue(result1)

    def test_odd_transaction_count(self):
        txs = [
            {"sender": "alice", "receiver": "bob", "amount": 10},
            {"sender": "bob", "receiver": "charlie", "amount": 5},
            {"sender": "charlie", "receiver": "dave", "amount": 3}
        ]
        tree = MerkleTree(txs)
        root = tree.get_merkle_root()
        self.assertIsNotNone(root)

    def test_proof_generation(self):
        txs = [
            {"sender": "alice", "receiver": "bob", "amount": 10},
            {"sender": "bob", "receiver": "charlie", "amount": 5},
            {"sender": "charlie", "receiver": "dave", "amount": 3},
            {"sender": "dave", "receiver": "eve", "amount": 1}
        ]
        tree = MerkleTree(txs)
        
        for i in range(len(txs)):
            proof = tree.get_proof(i)
            self.assertIsNotNone(proof)
            self.assertTrue(len(proof) > 0)

    def test_proof_verification(self):
        txs = [
            {"sender": "alice", "receiver": "bob", "amount": 10},
            {"sender": "bob", "receiver": "charlie", "amount": 5},
            {"sender": "charlie", "receiver": "dave", "amount": 3},
            {"sender": "dave", "receiver": "eve", "amount": 1}
        ]
        tree = MerkleTree(txs)
        root = tree.get_merkle_root()
        
        for i, tx_hash in enumerate(tree.tx_hashes):
            proof = tree.get_proof(i)
            result = MerkleTree.verify_proof(tx_hash, proof, root)
            self.assertTrue(result)

    def test_proof_verification_fails_wrong_root(self):
        txs = [
            {"sender": "alice", "receiver": "bob", "amount": 10},
            {"sender": "bob", "receiver": "charlie", "amount": 5}
        ]
        tree = MerkleTree(txs)
        
        wrong_root = "0" * 64
        tx_hash = tree.tx_hashes[0]
        proof = tree.get_proof(0)
        
        result = MerkleTree.verify_proof(tx_hash, proof, wrong_root)
        self.assertFalse(result)

    def test_proof_verification_fails_wrong_tx_hash(self):
        txs = [
            {"sender": "alice", "receiver": "bob", "amount": 10},
            {"sender": "bob", "receiver": "charlie", "amount": 5}
        ]
        tree = MerkleTree(txs)
        
        root = tree.get_merkle_root()
        proof = tree.get_proof(0)
        
        tampered_tx_hash = "a" * 64
        result = MerkleTree.verify_proof(tampered_tx_hash, proof, root)
        self.assertFalse(result)

    def test_calculate_merkle_root_matches_tree(self):
        txs = [
            {"sender": "alice", "receiver": "bob", "amount": 10},
            {"sender": "bob", "receiver": "charlie", "amount": 5},
            {"sender": "charlie", "receiver": "dave", "amount": 3}
        ]
        root1 = calculate_merkle_root(txs)
        root2 = MerkleTree(txs).get_merkle_root()
        self.assertEqual(root1, root2)

    def test_invalid_index(self):
        txs = [
            {"sender": "alice", "receiver": "bob", "amount": 10},
            {"sender": "bob", "receiver": "charlie", "amount": 5}
        ]
        tree = MerkleTree(txs)
        
        self.assertIsNone(tree.get_proof(10))
        self.assertIsNone(tree.get_proof(-1))


if __name__ == '__main__':
    unittest.main()
