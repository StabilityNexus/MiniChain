import tempfile
import shutil
import unittest
from unittest.mock import patch

from main import run_node
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

    async def start(self, port=9000, host="127.0.0.1", **kwargs):
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


class TestPersistenceRuntime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _append_empty_block(self, chain):
        from minichain.block import calculate_receipt_root

        block = Block(
            index=chain.last_block.index + 1,
            previous_hash=chain.last_block.hash,
            transactions=[],
            target=chain.current_target,
            state_root=chain.state.state_root(),
            receipt_root=calculate_receipt_root([]),
            receipts=[],
        )
        mine_block(block, target=chain.current_target)
        chain.add_block(block)
        return block

    async def test_run_node_loads_existing_sqlite_snapshot(self):
        chain = Blockchain()
        self._append_empty_block(chain)
        save(chain, self.tmpdir)

        async def fake_cli_loop(
            sk, pk, loaded_chain, mempool, network, datadir=None
        ):
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
        # Blockchain() initializes the protocol's predefined genesis accounts.
        # A fresh node should preserve that state, not an empty account map.
        expected_genesis_accounts = Blockchain().state.accounts

        async def fake_cli_loop(sk, pk, chain, mempool, network, datadir=None):
            self.assertEqual(chain.state.accounts, expected_genesis_accounts)

        with patch.object(main_module, "P2PNetwork", FakeNetwork), patch.object(
            main_module, "cli_loop", fake_cli_loop
        ):
            await main_module.run_node(
                port=9401,
                host="127.0.0.1",
                connect_to=None,
                fund=0,
                datadir=self.tmpdir,
            )

        restored = load(self.tmpdir)
        self.assertEqual(restored.state.accounts, expected_genesis_accounts)
        self.assertEqual(len(restored.chain), 1)
        self.assertEqual(restored.state.state_root(), restored.last_block.state_root)

    async def test_nonzero_fund_is_rejected_without_creating_snapshot(self):
        async def should_not_start(*args, **kwargs):
            self.fail("Node startup must stop before starting the network or CLI")

        with patch.object(main_module, "P2PNetwork", side_effect=AssertionError(
            "Network must not start when --fund is nonzero"
        )), patch.object(
            main_module, "cli_loop", side_effect=AssertionError(
                "CLI must not start when --fund is nonzero"
            )
        ), patch.object(
            main_module, "load_or_create_wallet", side_effect=AssertionError(
                "Wallet must not be loaded when --fund is nonzero"
            )
        ):
            await main_module.run_node(
                port=9402,
                host="127.0.0.1",
                connect_to=None,
                fund=50,
                datadir=self.tmpdir,
            )

        from minichain.persistence import persistence_exists
        self.assertFalse(persistence_exists(self.tmpdir))

    async def test_consensus_mined_block_is_accepted_by_unfunded_peer(self):
        node_chain = Blockchain()
        peer_chain = Blockchain()

        block = self._append_empty_block(node_chain)

        # A fresh peer starts from the same genesis state and must be able
        # to validate and apply the exact block without any out-of-band funds.
        peer_chain.add_block(block)

        self.assertEqual(peer_chain.last_block.hash, node_chain.last_block.hash)
        self.assertEqual(
            peer_chain.state.state_root(),
            node_chain.state.state_root(),
        )
        self.assertEqual(
            peer_chain.state.state_root(),
            peer_chain.last_block.state_root,
        )


if __name__ == "__main__":
    unittest.main()
