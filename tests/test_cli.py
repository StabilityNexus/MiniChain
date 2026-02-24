"""Unit tests for CLI command parsing and end-to-end command flow."""

from __future__ import annotations

import json
import os

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


def _extract_json_payload(text: str) -> dict[str, object]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("no JSON object found in output")
    return json.loads(text[start : end + 1])


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 7000
    assert args.command is None


def test_namespaced_parser_supports_wallet_commands() -> None:
    args = build_parser().parse_args(["wallet", "generate-key"])
    assert args.command == "wallet"
    assert args.wallet_command == "generate-key"
    assert args.action == "wallet_generate_key"


def test_parser_supports_node_run_flags() -> None:
    args = build_parser().parse_args(
        [
            "node",
            "run",
            "--peer",
            "127.0.0.1:7001",
            "--peer",
            "127.0.0.1:7002",
            "--mine",
        ]
    )
    assert args.action == "node_run"
    assert args.mine is True
    assert args.peer == ["127.0.0.1:7001", "127.0.0.1:7002"]


def test_parser_supports_node_stop_flags() -> None:
    args = build_parser().parse_args(["node", "stop", "--timeout-seconds", "2.5", "--force"])
    assert args.action == "node_stop"
    assert args.timeout_seconds == 2.5
    assert args.force is True


def test_top_level_help_shows_full_command_tree(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "Command Tree" in out
    assert "node run" in out
    assert "node stop" in out
    assert "wallet details" in out
    assert "chain accounts" in out


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


def test_shell_mode_executes_wallet_generate_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputs = iter(["wallet generate-key", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))

    main(["shell"])
    out = capsys.readouterr().out
    values = _parse_kv_lines(out)
    assert "private_key" in values
    assert "public_key" in values
    assert "address" in values


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

    main(["--data-dir", str(data_dir), "chain", "info"])
    chain_info = _parse_kv_lines(capsys.readouterr().out)
    assert chain_info["height"] == "1"
    assert len(chain_info["tip_hash"]) == 64
    assert chain_info["connected_peers"] == "0"

    main(["--data-dir", str(data_dir), "wallet", "balance", "--address", miner_address])
    balance_info = _parse_kv_lines(capsys.readouterr().out)
    assert balance_info["address"] == miner_address
    assert balance_info["balance"] == "50"
    assert balance_info["nonce"] == "0"

    main(["--data-dir", str(data_dir), "wallet", "details", "--address", miner_address])
    details = _parse_kv_lines(capsys.readouterr().out)
    assert details["exists"] == "true"

    main(["--data-dir", str(data_dir), "wallet", "list"])
    listed = capsys.readouterr().out
    assert "Wallet Accounts" in listed
    assert miner_address in listed


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
            "tx",
            "submit",
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

    main(["--data-dir", str(data_dir), "wallet", "balance", "--address", sender])
    sender_balance = _parse_kv_lines(capsys.readouterr().out)
    assert sender_balance["balance"] == "90"
    assert sender_balance["nonce"] == "1"

    main(["--data-dir", str(data_dir), "wallet", "balance", "--address", recipient])
    recipient_balance = _parse_kv_lines(capsys.readouterr().out)
    assert recipient_balance["balance"] == "10"
    assert recipient_balance["nonce"] == "0"

    main(["--data-dir", str(data_dir), "chain", "block", "--height", "2"])
    block_output = capsys.readouterr().out
    block_payload = _extract_json_payload(block_output)
    assert block_payload["header"]["block_height"] == 2
    assert len(block_payload["transactions"]) == 2


def test_chain_info_reads_connected_peers_from_daemon_runtime_status(
    tmp_path: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    miner_key, miner_verify = generate_key_pair()
    _ = miner_key
    miner_address = derive_address(miner_verify)
    data_dir = tmp_path / "daemon-status"
    data_dir.mkdir(parents=True, exist_ok=True)

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
    _ = capsys.readouterr()

    (data_dir / "node.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (data_dir / "node_runtime_status.json").write_text(
        json.dumps({"connected_peers": 2}),
        encoding="utf-8",
    )

    main(["--data-dir", str(data_dir), "chain", "info"])
    chain_info = _parse_kv_lines(capsys.readouterr().out)
    assert chain_info["height"] == "1"
    assert chain_info["connected_peers"] == "2"


def test_node_stop_removes_stale_pid_file(
    tmp_path: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "daemon"
    data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = data_dir / "node.pid"
    stale_pid = 999_999
    pid_file.write_text(f"{stale_pid}\n", encoding="utf-8")

    main(["--data-dir", str(data_dir), "node", "stop"])
    output = _parse_kv_lines(capsys.readouterr().out)
    assert output["status"] == "not_running"
    assert output["reason"] == "stale_pid_file_removed"
    assert output["stale_pid"] == str(stale_pid)
    assert not pid_file.exists()


def test_submit_tx_uses_network_path_when_daemon_running(
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    private_key_hex = serialize_signing_key(sender_key)
    data_dir = tmp_path / "daemon-node"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "node.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    captured: dict[str, object] = {}

    async def fake_submit_transaction_to_peer(*, transaction, host, port, timeout_seconds):
        captured["sender"] = transaction.sender
        captured["recipient"] = transaction.recipient
        captured["nonce"] = transaction.nonce
        captured["host"] = host
        captured["port"] = port
        captured["timeout_seconds"] = timeout_seconds
        return True, "accepted"

    monkeypatch.setattr(
        "minichain.__main__._submit_transaction_to_peer",
        fake_submit_transaction_to_peer,
    )
    monkeypatch.setattr(
        "minichain.__main__._infer_sender_nonce_from_data_dir",
        lambda **_kwargs: 5,
    )

    main(
        [
            "--data-dir",
            str(data_dir),
            "--host",
            "127.0.0.1",
            "--port",
            "7000",
            "tx",
            "submit",
            "--private-key",
            private_key_hex,
            "--recipient",
            recipient,
            "--amount",
            "3",
            "--fee",
            "1",
            "--no-mine-now",
        ]
    )
    submit_output = _parse_kv_lines(capsys.readouterr().out)
    assert submit_output["submitted_via"] == "network"
    assert submit_output["peer"] == "127.0.0.1:7000"
    assert submit_output["queued_in_mempool"] == "true"
    assert submit_output["nonce"] == "5"
    assert captured["sender"] == sender
    assert captured["recipient"] == recipient
    assert captured["nonce"] == 5
