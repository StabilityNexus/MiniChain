import unittest

import main


class TestCliArgs(unittest.TestCase):
    def test_rpc_host_defaults_to_loopback(self):
        """Bare-metal behavior is unchanged unless --rpc-host is passed."""
        args = main.build_arg_parser().parse_args([])
        self.assertEqual(args.rpc_host, "127.0.0.1")
        self.assertEqual(args.host, "127.0.0.1")

    def test_rpc_host_can_be_overridden(self):
        """--rpc-host lets the RPC server bind independently of --host."""
        args = main.build_arg_parser().parse_args(["--rpc-host", "0.0.0.0"])
        self.assertEqual(args.rpc_host, "0.0.0.0")

    def test_host_and_rpc_host_are_independent(self):
        args = main.build_arg_parser().parse_args(
            ["--host", "0.0.0.0", "--rpc-host", "127.0.0.1"]
        )
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.rpc_host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
