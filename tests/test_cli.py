"""Unit tests for CLI command parsing and end-to-end command flow."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nacl")

from minichain.__main__ import build_parser, main
from minichain.crypto import (
    derive_address,
    deserialize_signing_key,
    deserialize_verify_key,
    generate_key_pair,
    serialize_signing_key,
)


def _parse_kv_lines(text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in text.strip().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            pairs[key.strip()] = value.strip()
    return pairs


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 7000
    assert args.command is None


def test_generate_key_outputs_valid_material(capsys: pytest.CaptureFixture[str]) -> None:
    main(["generate-key"])
    out = capsys.readouterr().out
    values = _parse_kv_lines(out)

    assert "private_key" in values
    assert "public_key" in values
    assert "address" in values

    signing_key = deserialize_signing_key(values["private_key"])
    verify_key = deserialize_verify_key(values["public_key"])
    assert signing_key.verify_key == verify_key
    assert derive_address(verify_key) == values["address"]


def test_mine_chain_info_and_balance_commands(
    tmp_path: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    miner_key, miner_verify = generate_key_pair()
    _ = miner_key
    miner_address = derive_address(miner_verify)
    data_dir = tmp_path / "cli-node"

    main(
        [
            "--data-dir",
            str(data_dir),
            "--miner-address",
            miner_address,
            "mine",
            "--count",
            "1",
        ]
    )
    mined_output = capsys.readouterr().out
    assert "mined_block_1=height:1" in mined_output

    main(["--data-dir", str(data_dir), "chain-info"])
    chain_info = _parse_kv_lines(capsys.readouterr().out)
    assert chain_info["height"] == "1"
    assert len(chain_info["tip_hash"]) == 64

    main(["--data-dir", str(data_dir), "balance", "--address", miner_address])
    balance_info = _parse_kv_lines(capsys.readouterr().out)
    assert balance_info["address"] == miner_address
    assert balance_info["balance"] == "50"
    assert balance_info["nonce"] == "0"


def test_submit_tx_then_query_balances_and_block(
    tmp_path: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    private_key_hex = serialize_signing_key(sender_key)
    data_dir = tmp_path / "cli-node"

    main(
        [
            "--data-dir",
            str(data_dir),
            "--miner-address",
            sender,
            "mine",
            "--count",
            "1",
        ]
    )
    _ = capsys.readouterr()

    main(
        [
            "--data-dir",
            str(data_dir),
            "submit-tx",
            "--private-key",
            private_key_hex,
            "--recipient",
            recipient,
            "--amount",
            "10",
            "--fee",
            "2",
        ]
    )
    submit_output = _parse_kv_lines(capsys.readouterr().out)
    assert "submitted_tx_id" in submit_output
    assert submit_output["sender"] == sender
    assert submit_output["recipient"] == recipient
    assert submit_output["mined_block_height"] == "2"

    main(["--data-dir", str(data_dir), "balance", "--address", sender])
    sender_balance = _parse_kv_lines(capsys.readouterr().out)
    assert sender_balance["balance"] == "90"
    assert sender_balance["nonce"] == "1"

    main(["--data-dir", str(data_dir), "balance", "--address", recipient])
    recipient_balance = _parse_kv_lines(capsys.readouterr().out)
    assert recipient_balance["balance"] == "10"
    assert recipient_balance["nonce"] == "0"

    main(["--data-dir", str(data_dir), "block", "--height", "2"])
    block_payload = json.loads(capsys.readouterr().out.strip())
    assert block_payload["header"]["block_height"] == 2
    assert len(block_payload["transactions"]) == 2
