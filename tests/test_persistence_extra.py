"""
tests/test_persistence_extra.py

Additional coverage for minichain.persistence beyond the round-trip tests in
test_persistence.py: legacy-JSON validation branches, genesis/db-corruption
edge cases, and the banned-peers table (previously untested).
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from minichain import Blockchain
from minichain.persistence import (
    ban_peer,
    get_banned_peers,
    is_peer_banned,
    load,
    save,
    unban_peer,
)
from minichain.pow import calculate_hash

DB_FILE = "data.db"
LEGACY_FILE = "data.json"


def _header_dict_from_payload(payload):
    """Rebuild the header dict Block.to_header_dict() would produce from a
    saved block payload, so a modified field's hash can be recomputed and
    stay internally self-consistent (i.e. survive Block.from_dict's own
    hash check and only fail the *chain-linkage* check in persistence.py)."""
    header = {
        "index": payload["index"],
        "previous_hash": payload["previous_hash"],
        "merkle_root": payload.get("merkle_root"),
        "state_root": payload.get("state_root"),
        "receipt_root": payload.get("receipt_root"),
        "timestamp": payload["timestamp"],
        "target": payload["target"],
        "nonce": payload["nonce"],
    }
    if payload.get("miner") is not None:
        header["miner"] = payload["miner"]
    return header


class TestLegacyJsonValidation(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_legacy(self, payload):
        with open(os.path.join(self.tmpdir, LEGACY_FILE), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_non_dict_snapshot_rejected(self):
        self._write_legacy(["not", "a", "dict"])
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_empty_chain_rejected(self):
        self._write_legacy({"chain": [], "state": {}})
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_non_list_chain_rejected(self):
        self._write_legacy({"chain": "not-a-list", "state": {}})
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_non_dict_state_rejected(self):
        bc = Blockchain()
        self._write_legacy({"chain": [bc.chain[0].to_dict()], "state": "not-a-dict"})
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)


class TestSqliteEdgeCases(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_non_genesis_first_block_rejected(self):
        """genesis.index must be 0. Recompute the hash after tampering so the
        block stays internally self-consistent and the failure comes from
        _verify_chain_integrity's genesis check, not Block.from_dict's own
        hash-mismatch guard."""
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT block_json FROM blocks WHERE height = 0").fetchone()
            payload = json.loads(row[0])
            payload["index"] = 1  # genesis must be index 0
            payload["hash"] = calculate_hash(_header_dict_from_payload(payload))
            conn.execute(
                "UPDATE blocks SET block_json = ? WHERE height = 0",
                (json.dumps(payload),),
            )
        with self.assertRaisesRegex(ValueError, "Invalid genesis block"):
            load(path=self.tmpdir)

    def test_garbage_database_file_raises(self):
        """Garbage bytes alone don't fail until a real query touches the file
        (PRAGMA foreign_keys doesn't validate file content), so this exercises
        the ValueError raised from the query-level DatabaseError handler."""
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with open(db_path, "wb") as f:
            f.write(b"this is not a sqlite database")
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_unopenable_database_path_raises(self):
        """A path sqlite can't even open (here: a directory, not a file) fails
        at connect() itself -- the earlier of persistence.py's two
        DatabaseError-to-ValueError translations."""
        db_path = os.path.join(self.tmpdir, DB_FILE)
        os.makedirs(db_path)
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_broken_previous_hash_link_rejected_at_chain_integrity_check(self):
        """A block whose own hash is internally valid, but whose previous_hash
        doesn't match the prior block's actual hash, must be caught by
        _verify_chain_integrity's linkage check (not Block.from_dict's
        per-block hash guard, which a naive tamper would trip instead)."""
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)

        # Add a second, self-consistent block whose previous_hash points nowhere real.
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT block_json FROM blocks WHERE height = 0").fetchone()
            genesis_payload = json.loads(row[0])

            bogus_payload = dict(genesis_payload)
            bogus_payload["index"] = 1
            bogus_payload["previous_hash"] = "0" * 64  # does not match genesis.hash
            bogus_payload["timestamp"] = genesis_payload["timestamp"] + 1000
            bogus_payload["hash"] = calculate_hash(_header_dict_from_payload(bogus_payload))

            conn.execute(
                "INSERT INTO blocks (height, block_json) VALUES (?, ?)",
                (1, json.dumps(bogus_payload)),
            )
            conn.execute("UPDATE metadata SET value = '2' WHERE key = 'chain_length'")

        with self.assertRaisesRegex(ValueError, r"Block #1:.*invalid previous hash"):
            load(path=self.tmpdir)

    def test_missing_chain_length_metadata_raises(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM metadata WHERE key = 'chain_length'")
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_non_integer_chain_length_raises(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE metadata SET value = 'not-a-number' WHERE key = 'chain_length'"
            )
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_mismatched_chain_length_raises(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE metadata SET value = '99' WHERE key = 'chain_length'")
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)


class TestBannedPeers(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_peer_not_banned_before_any_db_exists(self):
        """A read against a datadir with no persistence file yet must short-circuit
        to a safe default rather than creating the file or raising."""
        self.assertFalse(is_peer_banned("peerA", self.tmpdir))
        self.assertEqual(get_banned_peers(self.tmpdir), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, DB_FILE)))

    def test_ban_then_check_and_list(self):
        ban_peer("peerA", "spamming blocks", self.tmpdir)

        self.assertTrue(is_peer_banned("peerA", self.tmpdir))
        self.assertFalse(is_peer_banned("peerB", self.tmpdir))

        banned = get_banned_peers(self.tmpdir)
        self.assertEqual(len(banned), 1)
        self.assertEqual(banned[0]["peer_id"], "peerA")
        self.assertEqual(banned[0]["reason"], "spamming blocks")

    def test_reban_replaces_reason(self):
        ban_peer("peerA", "first reason", self.tmpdir)
        ban_peer("peerA", "second reason", self.tmpdir)

        banned = get_banned_peers(self.tmpdir)
        self.assertEqual(len(banned), 1)
        self.assertEqual(banned[0]["reason"], "second reason")

    def test_unban_removes_peer(self):
        ban_peer("peerA", "temp ban", self.tmpdir)
        self.assertTrue(is_peer_banned("peerA", self.tmpdir))

        unban_peer("peerA", self.tmpdir)

        self.assertFalse(is_peer_banned("peerA", self.tmpdir))
        self.assertEqual(get_banned_peers(self.tmpdir), [])

    def test_unban_nonexistent_peer_is_a_noop(self):
        """Unbanning a peer that was never banned, with no DB present yet, must
        short-circuit without creating a database or raising."""
        unban_peer("never-banned", self.tmpdir)
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, DB_FILE)))


if __name__ == "__main__":
    unittest.main()
