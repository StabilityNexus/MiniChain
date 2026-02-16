"""CLI entrypoint for running a MiniChain node."""

from __future__ import annotations

import argparse

from minichain.node import start_node


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a MiniChain node.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for the node")
    parser.add_argument("--port", default=7000, type=int, help="Port for the node")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    start_node(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
