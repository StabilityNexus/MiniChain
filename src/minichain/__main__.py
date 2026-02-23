"""CLI entrypoint for running a MiniChain node."""

from __future__ import annotations

import argparse
from pathlib import Path

from minichain.node import MiniChainNode, NodeConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a MiniChain node.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for the node")
    parser.add_argument("--port", default=7000, type=int, help="Port for the node")
    parser.add_argument(
        "--data-dir",
        default=".minichain",
        help="Directory for node data (sqlite db, chain state)",
    )
    parser.add_argument(
        "--miner-address",
        default=None,
        help="Optional 20-byte lowercase hex address used for mining rewards",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    node = MiniChainNode(
        NodeConfig(
            data_dir=Path(args.data_dir),
            miner_address=args.miner_address,
        )
    )
    node.start()
    try:
        print(f"MiniChain node started on {args.host}:{args.port}")
        print(f"chain_height={node.height} tip={node.tip_hash}")
    finally:
        node.stop()


if __name__ == "__main__":
    main()
