"""CLI entrypoint for running a MiniChain node."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from minichain.crypto import (
    derive_address,
    deserialize_signing_key,
    generate_key_pair,
    serialize_signing_key,
    serialize_verify_key,
)
from minichain.node import MiniChainNode, NodeConfig
from minichain.transaction import ADDRESS_HEX_LENGTH, Transaction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MiniChain CLI.")
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
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("start", help="Start node and print current status")
    subparsers.add_parser("generate-key", help="Generate a new keypair and address")

    balance = subparsers.add_parser("balance", help="Query account balance and nonce")
    balance.add_argument("--address", required=True, help="20-byte lowercase hex address")

    submit_tx = subparsers.add_parser("submit-tx", help="Submit a signed transfer transaction")
    submit_tx.add_argument("--private-key", required=True, help="hex-encoded Ed25519 signing key")
    submit_tx.add_argument("--recipient", required=True, help="20-byte lowercase hex address")
    submit_tx.add_argument("--amount", required=True, type=int, help="transfer amount")
    submit_tx.add_argument("--fee", default=1, type=int, help="transaction fee")
    submit_tx.add_argument("--nonce", default=None, type=int, help="optional sender nonce")
    submit_tx.add_argument(
        "--mine-now",
        action="store_true",
        help="mine one block immediately after submission (default behavior)",
    )
    submit_tx.add_argument(
        "--no-mine-now",
        action="store_false",
        dest="mine_now",
        help="do not mine immediately after submission",
    )
    submit_tx.set_defaults(mine_now=True)

    block = subparsers.add_parser("block", help="Query a block by height or hash")
    block_group = block.add_mutually_exclusive_group(required=True)
    block_group.add_argument("--height", type=int, help="block height")
    block_group.add_argument("--hash", dest="block_hash", help="block hash (hex)")

    mine = subparsers.add_parser("mine", help="Mine one or more blocks")
    mine.add_argument("--count", default=1, type=int, help="number of blocks to mine")
    mine.add_argument(
        "--max-transactions",
        default=None,
        type=int,
        help="max non-coinbase tx per block",
    )

    subparsers.add_parser("chain-info", help="Query chain height and canonical tip hash")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    command = args.command or "start"

    if command == "generate-key":
        _run_generate_key()
        return

    miner_address = args.miner_address
    if command == "submit-tx" and args.mine_now and miner_address is None:
        inferred = _infer_sender_from_private_key(args.private_key)
        miner_address = inferred

    node = MiniChainNode(
        NodeConfig(
            data_dir=Path(args.data_dir),
            miner_address=miner_address,
        )
    )
    node.start()
    try:
        if command == "start":
            print(f"MiniChain node started on {args.host}:{args.port}")
            print(f"chain_height={node.height} tip={node.tip_hash}")
            return

        if command == "balance":
            _run_balance(node=node, address=args.address)
            return

        if command == "chain-info":
            _run_chain_info(node=node)
            return

        if command == "block":
            _run_block_query(node=node, height=args.height, block_hash=args.block_hash)
            return

        if command == "submit-tx":
            _run_submit_transaction(
                node=node,
                private_key_hex=args.private_key,
                recipient=args.recipient,
                amount=args.amount,
                fee=args.fee,
                nonce=args.nonce,
                mine_now=args.mine_now,
            )
            return

        if command == "mine":
            _run_mine(
                node=node,
                count=args.count,
                max_transactions=args.max_transactions,
            )
            return

        raise ValueError(f"Unsupported command: {command}")
    finally:
        node.stop()


def _run_generate_key() -> None:
    signing_key, verify_key = generate_key_pair()
    private_key = serialize_signing_key(signing_key)
    public_key = serialize_verify_key(verify_key)
    address = derive_address(verify_key)
    print(f"private_key={private_key}")
    print(f"public_key={public_key}")
    print(f"address={address}")


def _run_balance(*, node: MiniChainNode, address: str) -> None:
    if not _is_lower_hex(address, ADDRESS_HEX_LENGTH):
        raise ValueError("address must be a 20-byte lowercase hex string")
    account = node.chain_manager.state.get_account(address)
    print(f"address={address}")
    print(f"balance={account.balance}")
    print(f"nonce={account.nonce}")


def _run_chain_info(*, node: MiniChainNode) -> None:
    print(f"height={node.height}")
    print(f"tip_hash={node.tip_hash}")


def _run_block_query(
    *,
    node: MiniChainNode,
    height: int | None,
    block_hash: str | None,
) -> None:
    if height is not None:
        block = node.storage.get_block_by_height(height)
    else:
        if block_hash is None:
            raise ValueError("block hash is required")
        block = node.storage.get_block_by_hash(block_hash)

    if block is None:
        print("block_not_found")
        return

    payload = {
        "hash": block.hash().hex(),
        "header": asdict(block.header),
        "transactions": [asdict(transaction) for transaction in block.transactions],
    }
    print(json.dumps(payload, sort_keys=True))


def _run_submit_transaction(
    *,
    node: MiniChainNode,
    private_key_hex: str,
    recipient: str,
    amount: int,
    fee: int,
    nonce: int | None,
    mine_now: bool,
) -> None:
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if fee < 0:
        raise ValueError("fee must be non-negative")
    if not _is_lower_hex(recipient, ADDRESS_HEX_LENGTH):
        raise ValueError("recipient must be a 20-byte lowercase hex string")

    signing_key = deserialize_signing_key(private_key_hex)
    sender_address = derive_address(signing_key.verify_key)
    sender_account = node.chain_manager.state.get_account(sender_address)
    resolved_nonce = sender_account.nonce if nonce is None else nonce
    if resolved_nonce < 0:
        raise ValueError("nonce must be non-negative")

    transaction = Transaction(
        sender=sender_address,
        recipient=recipient,
        amount=amount,
        nonce=resolved_nonce,
        fee=fee,
        timestamp=int(time.time()),
    )
    transaction.sign(signing_key)

    transaction_id = node.submit_transaction(transaction)
    print(f"submitted_tx_id={transaction_id}")
    print(f"sender={sender_address}")
    print(f"recipient={recipient}")

    if not mine_now:
        print("queued_in_mempool=true")
        return

    mined_block = node.mine_one_block()
    print(f"mined_block_height={mined_block.header.block_height}")
    print(f"mined_block_hash={mined_block.hash().hex()}")


def _run_mine(
    *,
    node: MiniChainNode,
    count: int,
    max_transactions: int | None,
) -> None:
    if count <= 0:
        raise ValueError("count must be positive")
    for index in range(1, count + 1):
        block = node.mine_one_block(max_transactions=max_transactions)
        print(
            f"mined_block_{index}=height:{block.header.block_height},hash:{block.hash().hex()}"
        )


def _infer_sender_from_private_key(private_key_hex: str) -> str:
    signing_key = deserialize_signing_key(private_key_hex)
    return derive_address(signing_key.verify_key)


def _is_lower_hex(value: str, expected_length: int) -> bool:
    if len(value) != expected_length:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


if __name__ == "__main__":
    main()
