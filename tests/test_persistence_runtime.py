import tempfile
import shutil
import unittest
from unittest.mock import patch

from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

import main as main_module
from minichain import Blockchain, Block, Transaction, mine_block
from minichain.persistence import load, save


class FakeNetwork:
    def __init__(self, **kwargs):
        self.handler = None
        self.peer_count = 0
        self._on_peer_connected = None

    def register_handler(self, handler):
        self.handler = handler

    def register_on_peer_connected(self, callback):
        self._on_peer_connected = callback

    async def start(self, port=9000, host="127.0.0.1"):
        self.port = port
        self.host = host

    async def stop(self):
        return None

    async def connect_to_peer(self, host, port):
        self.peer_count += 1
        return True

    async def broadcast_transaction(self, tx):
        return None

    async def broadcast_block(self, block, miner=None):
        return None


def _make_keypair():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


class TestPersistenceRuntime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _chain_with_tx(self):
        bc = Blockchain()
        _, miner_pk = _make_keypair()

        # Mine a valid empty block (coinbase) so the chain has 2 blocks.
        # Using an empty block avoids the need to pre-fund any sender outside
        # of a properly validated block.
        from minichain.state import State
        temp_state = bc.state.copy()
        temp_state.chain_id = bc.chain_id
        temp_state.credit_mining_reward(miner_pk, reward=temp_state.DEFAULT_MINING_REWARD)

        from minichain.block import calculate_receipt_root
        block = Block(
            index=1,
            previous_hash=bc.last_block.hash,
            transactions=[],
            difficulty=bc.current_difficulty,
            state_root=temp_state.state_root(),
            receipt_root=None,
            receipts=[],
            miner=miner_pk,
        )
        mine_block(block, difficulty=bc.current_difficulty)
        bc.add_block(block)
        return bc

    async def test_run_node_loads_existing_sqlite_snapshot(self):
        chain = self._chain_with_tx()
        save(chain, self.tmpdir)

        async def fake_cli_loop(sk, pk, loaded_chain, mempool, network, datadir=None):
            self.assertEqual(len(loaded_chain.chain), len(chain.chain))
            self.assertEqual(loaded_chain.last_block.hash, chain.last_block.hash)
            self.assertEqual(loaded_chain.state.accounts, chain.state.accounts)

        with patch.object(main_module, "P2PNetwork", FakeNetwork), patch.object(
            main_module, "cli_loop", fake_cli_loop
        ):
            await main_module.run_node(
                port=9400,
                host="127.0.0.1",
                connect_to=None,
                fund=0,
                datadir=self.tmpdir,
            )

    async def test_run_node_saves_sqlite_snapshot_on_shutdown(self):
        """Verify that the node saves a snapshot on shutdown.

        The ``fund=25`` balance is a dev-only convenience injected directly
        into state outside of any block.  It is intentionally NOT replayed by
        load() because the new load() only reconstructs state through
        _apply_block(), which is the correct, secure behaviour.
        We therefore only verify that the snapshot was written and the chain
        structure survives the round-trip.
        """
        fixed_sk, fixed_pk = _make_keypair()

        async def fake_cli_loop(sk, pk, chain, mempool, network, datadir=None):
            self.assertEqual(pk, fixed_pk)
            self.assertEqual(chain.state.get_account(pk)["balance"], 25)

        with patch.object(main_module, "P2PNetwork", FakeNetwork), patch.object(
            main_module, "cli_loop", fake_cli_loop
        ), patch.object(main_module, "load_or_create_wallet", return_value=(fixed_sk, fixed_pk)):
            await main_module.run_node(
                port=9401,
                host="127.0.0.1",
                connect_to=None,
                fund=25,
                datadir=self.tmpdir,
            )

        # The snapshot was saved.  load() replays through _apply_block(), so
        # only block-committed state survives. The genesis-only chain (no
        # blocks added in cli_loop) is intact.
        restored = load(self.tmpdir)
        self.assertEqual(len(restored.chain), 1)
        self.assertEqual(restored.chain[0].hash, Blockchain().chain[0].hash)


if __name__ == "__main__":
    unittest.main()
