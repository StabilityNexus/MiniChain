import hashlib
import json
from typing import Optional, List

def hash_data(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def to_nibbles(key_hex: str) -> List[int]:
    """Converts a hex string key into a list of integer nibbles (0-15)."""
    try:
        return [int(c, 16) for c in key_hex]
    except ValueError:
        raise ValueError(f"Invalid MPT key: '{key_hex}'. Keys must be valid hex strings.")

class Node:
    def hash(self) -> bytes:
        raise NotImplementedError

class LeafNode(Node):
    def __init__(self, path: List[int], value: str):
        self.path = path
        self.value = value

    def hash(self) -> bytes:
        data = json.dumps({"type": "leaf", "path": self.path, "value": self.value}, sort_keys=True)
        return hash_data(data.encode())

class ExtensionNode(Node):
    def __init__(self, path: List[int], child: Node):
        self.path = path
        self.child = child

    def hash(self) -> bytes:
        child_hash = self.child.hash().hex()
        data = json.dumps({"type": "extension", "path": self.path, "child": child_hash}, sort_keys=True)
        return hash_data(data.encode())

class BranchNode(Node):
    def __init__(self):
        self.branches: List[Optional[Node]] = [None] * 16
        self.value: Optional[str] = None

    def hash(self) -> bytes:
        b_hashes = [b.hash().hex() if b else None for b in self.branches]
        data = json.dumps({"type": "branch", "branches": b_hashes, "value": self.value}, sort_keys=True)
        return hash_data(data.encode())

class Trie:
    """
    A simplified Merkle Patricia Trie (MPT) for MiniChain.
    Provides O(log N) state verification via cryptographic state roots.
    """
    def __init__(self):
        self.root: Optional[Node] = None

    def root_hash(self) -> str:
        """Returns the 32-byte hex hash of the trie's root."""
        if not self.root:
            return "0" * 64
        return self.root.hash().hex()

    def get(self, key_hex: str) -> Optional[str]:
        if not self.root:
            return None
        return self._get(self.root, to_nibbles(key_hex))

    def _get(self, node: Optional[Node], path: List[int]) -> Optional[str]:
        if not node:
            return None
            
        if isinstance(node, LeafNode):
            if node.path == path:
                return node.value
            return None
            
        elif isinstance(node, ExtensionNode):
            if path[:len(node.path)] == node.path:
                return self._get(node.child, path[len(node.path):])
            return None
            
        elif isinstance(node, BranchNode):
            if not path:
                return node.value
            nibble = path[0]
            return self._get(node.branches[nibble], path[1:])
            
        return None

    def put(self, key_hex: str, value: str):
        path = to_nibbles(key_hex)
        self.root = self._put(self.root, path, value)

    def _put(self, node: Optional[Node], path: List[int], value: str) -> Node:
        if node is None:
            return LeafNode(path, value)

        if isinstance(node, LeafNode):
            if node.path == path:
                node.value = value
                return node
            
            # Paths diverge. Find common prefix.
            common = 0
            while common < len(node.path) and common < len(path) and node.path[common] == path[common]:
                common += 1
            
            branch = BranchNode()
            
            # Handle the leaf's remaining path
            leaf_remaining = node.path[common:]
            if not leaf_remaining:
                branch.value = node.value
            else:
                branch.branches[leaf_remaining[0]] = LeafNode(leaf_remaining[1:], node.value)
                
            # Handle the new value's remaining path
            new_remaining = path[common:]
            if not new_remaining:
                branch.value = value
            else:
                branch.branches[new_remaining[0]] = LeafNode(new_remaining[1:], value)
                
            if common > 0:
                return ExtensionNode(node.path[:common], branch)
            return branch

        elif isinstance(node, ExtensionNode):
            common = 0
            while common < len(node.path) and common < len(path) and node.path[common] == path[common]:
                common += 1
            
            if common == len(node.path):
                # Path matches extension exactly, continue to child
                node.child = self._put(node.child, path[common:], value)
                return node
                
            # Divergence inside the extension node
            branch = BranchNode()
            ext_remaining = node.path[common:]
            
            # The child of the extension becomes a branch's branch
            if len(ext_remaining) == 1:
                branch.branches[ext_remaining[0]] = node.child
            else:
                branch.branches[ext_remaining[0]] = ExtensionNode(ext_remaining[1:], node.child)
                
            # Insert the new value
            new_remaining = path[common:]
            if not new_remaining:
                branch.value = value
            else:
                branch.branches[new_remaining[0]] = LeafNode(new_remaining[1:], value)
                
            if common > 0:
                return ExtensionNode(node.path[:common], branch)
            return branch
            
        elif isinstance(node, BranchNode):
            if not path:
                node.value = value
            else:
                nibble = path[0]
                node.branches[nibble] = self._put(node.branches[nibble], path[1:], value)
            return node
