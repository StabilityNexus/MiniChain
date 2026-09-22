import os
import shutil
import tempfile
import unittest

from libp2p.peer.id import ID

from minichain.identity import load_or_create_keypair
from minichain.persistence import get_known_peers, remember_peer


class TestPersistentIdentity(unittest.TestCase):
    def setUp(self):
        self.datadir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.datadir, ignore_errors=True)

    def test_keypair_survives_reload(self):
        """A node must present the same peer ID across restarts, or every address
        a peer saved for it (and every address it saved for a peer) goes stale."""
        first = load_or_create_keypair(self.datadir)
        second = load_or_create_keypair(self.datadir)

        self.assertEqual(
            ID.from_pubkey(first.public_key),
            ID.from_pubkey(second.public_key),
        )

    def test_nodekey_file_is_created(self):
        load_or_create_keypair(self.datadir)

        self.assertTrue(os.path.exists(os.path.join(self.datadir, "nodekey")))

    def test_corrupt_nodekey_falls_back_to_a_new_identity(self):
        """A damaged key file must not crash the node on startup."""
        nodekey_path = os.path.join(self.datadir, "nodekey")
        os.makedirs(self.datadir, exist_ok=True)
        with open(nodekey_path, "wb") as f:
            f.write(b"not a valid seed")

        keypair = load_or_create_keypair(self.datadir)

        self.assertIsNotNone(keypair)


class TestPeerBook(unittest.TestCase):
    def setUp(self):
        self.datadir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.datadir, ignore_errors=True)

    def test_empty_peer_book_before_any_peer_is_remembered(self):
        self.assertEqual(get_known_peers(self.datadir), [])

    def test_remember_and_retrieve_peer(self):
        remember_peer("peerA", "/ip4/1.2.3.4/tcp/9000/p2p/peerA", self.datadir)

        known = get_known_peers(self.datadir)

        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]["peer_id"], "peerA")
        self.assertEqual(known[0]["multiaddr"], "/ip4/1.2.3.4/tcp/9000/p2p/peerA")

    def test_remembering_a_peer_again_updates_its_address(self):
        """A peer's multiaddr can change (different port, moved networks); the
        book must track the latest one rather than accumulate stale entries."""
        remember_peer("peerA", "/ip4/1.2.3.4/tcp/9000/p2p/peerA", self.datadir)
        remember_peer("peerA", "/ip4/1.2.3.4/tcp/9001/p2p/peerA", self.datadir)

        known = get_known_peers(self.datadir)

        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]["multiaddr"], "/ip4/1.2.3.4/tcp/9001/p2p/peerA")

    def test_multiple_peers_are_all_retrievable(self):
        remember_peer("peerA", "/ip4/1.2.3.4/tcp/9000/p2p/peerA", self.datadir)
        remember_peer("peerB", "/ip4/5.6.7.8/tcp/9000/p2p/peerB", self.datadir)

        known = get_known_peers(self.datadir)

        self.assertEqual({p["peer_id"] for p in known}, {"peerA", "peerB"})

    def test_limit_caps_the_number_returned(self):
        for i in range(5):
            remember_peer(f"peer{i}", f"/ip4/1.2.3.4/tcp/900{i}/p2p/peer{i}", self.datadir)

        self.assertEqual(len(get_known_peers(self.datadir, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
