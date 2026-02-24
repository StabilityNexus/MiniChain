"""CLI entrypoint for running and interacting with MiniChain."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import signal
import textwrap
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
from minichain.network import (
    TX_GOSSIP_PROTOCOL_ID,
    MiniChainNetwork,
    NetworkConfig,
    PeerAddress,
)
from minichain.node import MiniChainNode, NodeConfig, NodeError
from minichain.transaction import ADDRESS_HEX_LENGTH, Transaction

DEFAULT_DATA_DIR = os.environ.get("MINICHAIN_DATA_DIR", str(Path.home() / ".minichain"))
NODE_PID_FILENAME = "node.pid"
NODE_RUNTIME_STATUS_FILENAME = "node_runtime_status.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MiniChain CLI.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Command Tree
              node start
              node run [--peer host:port] [--mine]
              node stop [--timeout-seconds N] [--force]
              wallet generate-key
              wallet balance --address <addr>
              wallet details --address <addr>
              wallet list [--limit N]
              tx submit --private-key <pk> --recipient <addr> --amount N --fee N [--nonce N]
              chain info
              chain block --height <n> | --hash <hex>
              chain accounts [--limit N]
              mine --count N [--max-transactions N]
              shell

            Legacy commands still work (auto-remapped):
              start, generate-key, balance, submit-tx, chain-info, block
            """
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for the node")
    parser.add_argument("--port", default=7000, type=int, help="Port for the node")
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help="Directory for node data (sqlite db, chain state). "
        "Default: $MINICHAIN_DATA_DIR or ~/.minichain",
    )
    parser.add_argument(
        "--miner-address",
        default=None,
        help="Optional 20-byte lowercase hex address used for mining rewards",
    )

    subparsers = parser.add_subparsers(dest="command")

    _add_node_group(subparsers)
    _add_wallet_group(subparsers)
    _add_tx_group(subparsers)
    _add_chain_group(subparsers)
    _add_mine_command(subparsers)

    shell = subparsers.add_parser("shell", help="Run interactive MiniChain CLI shell")
    shell.set_defaults(action="shell")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    normalized_argv = _normalize_cli_tokens(argv)
    args = parser.parse_args(normalized_argv)
    action = getattr(args, "action", None)

    if action is None:
        args.action = "node_start"
        action = "node_start"

    if action == "shell":
        _run_shell(parser, args)
        return

    _execute_action(args)


def _add_node_group(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    node = subparsers.add_parser("node", help="Node lifecycle commands")
    node_sub = node.add_subparsers(dest="node_command", required=True)
    node_start = node_sub.add_parser("start", help="Start node and print chain status")
    node_start.set_defaults(action="node_start")

    node_run = node_sub.add_parser(
        "run",
        help="Run long-lived node daemon with networking/sync (Ctrl+C to stop)",
    )
    node_run.add_argument(
        "--peer",
        action="append",
        default=[],
        help="Bootstrap peer in host:port format (repeat flag to add more peers)",
    )
    node_run.add_argument(
        "--advertise-host",
        default=None,
        help="Host/IP advertised to peers (defaults to --host)",
    )
    node_run.add_argument(
        "--node-id",
        default=None,
        help="Optional stable node id string for logs/debugging",
    )
    node_run.add_argument(
        "--mdns",
        action="store_true",
        help="Enable local mDNS discovery in addition to bootstrap peers",
    )
    node_run.add_argument(
        "--mine",
        action="store_true",
        help="Enable continuous mining loop",
    )
    node_run.add_argument(
        "--mine-interval-seconds",
        type=float,
        default=2.0,
        help="Sleep interval between mined blocks in daemon mode",
    )
    node_run.add_argument(
        "--sync-batch-size",
        type=int,
        default=128,
        help="Max blocks transferred per sync batch",
    )
    node_run.add_argument(
        "--status-interval-seconds",
        type=float,
        default=5.0,
        help="Periodic status-log interval in seconds",
    )
    node_run.set_defaults(action="node_run")

    node_stop = node_sub.add_parser(
        "stop",
        help="Stop daemon node for this data directory via PID file",
    )
    node_stop.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="Graceful stop wait timeout before optional force-stop",
    )
    node_stop.add_argument(
        "--force",
        action="store_true",
        help="Send SIGKILL if graceful stop does not finish in timeout",
    )
    node_stop.set_defaults(action="node_stop")


def _add_wallet_group(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    wallet = subparsers.add_parser("wallet", help="Wallet and account commands")
    wallet_sub = wallet.add_subparsers(dest="wallet_command", required=True)

    wallet_generate = wallet_sub.add_parser("generate-key", help="Generate a new keypair")
    wallet_generate.set_defaults(action="wallet_generate_key")

    wallet_balance = wallet_sub.add_parser("balance", help="Query account balance and nonce")
    wallet_balance.add_argument("--address", required=True, help="20-byte lowercase hex address")
    wallet_balance.set_defaults(action="wallet_balance")

    wallet_details = wallet_sub.add_parser(
        "details",
        help="Query account details with existence flag",
    )
    wallet_details.add_argument("--address", required=True, help="20-byte lowercase hex address")
    wallet_details.set_defaults(action="wallet_details")

    wallet_list = wallet_sub.add_parser("list", help="List known accounts in current state")
    wallet_list.add_argument("--limit", type=int, default=100, help="max accounts to print")
    wallet_list.set_defaults(action="wallet_list")


def _add_tx_group(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tx = subparsers.add_parser("tx", help="Transaction commands")
    tx_sub = tx.add_subparsers(dest="tx_command", required=True)

    tx_submit = tx_sub.add_parser("submit", help="Submit a signed transfer transaction")
    _add_submit_tx_args(tx_submit)
    tx_submit.set_defaults(action="tx_submit")


def _add_chain_group(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    chain = subparsers.add_parser("chain", help="Chain query commands")
    chain_sub = chain.add_subparsers(dest="chain_command", required=True)

    chain_info = chain_sub.add_parser("info", help="Query chain height and canonical tip hash")
    chain_info.set_defaults(action="chain_info")

    block = chain_sub.add_parser("block", help="Query a block by height or hash")
    block_group = block.add_mutually_exclusive_group(required=True)
    block_group.add_argument("--height", type=int, help="block height")
    block_group.add_argument("--hash", dest="block_hash", help="block hash (hex)")
    block.set_defaults(action="chain_block")

    accounts = chain_sub.add_parser("accounts", help="List known chain accounts")
    accounts.add_argument("--limit", type=int, default=100, help="max accounts to print")
    accounts.set_defaults(action="chain_accounts")


def _add_mine_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    mine = subparsers.add_parser("mine", help="Mine one or more blocks")
    mine.add_argument("--count", default=1, type=int, help="number of blocks to mine")
    mine.add_argument(
        "--max-transactions",
        default=None,
        type=int,
        help="max non-coinbase tx per block",
    )
    mine.set_defaults(action="mine")


def _add_submit_tx_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--private-key", required=True, help="hex-encoded Ed25519 signing key")
    parser.add_argument("--recipient", required=True, help="20-byte lowercase hex address")
    parser.add_argument("--amount", required=True, type=int, help="transfer amount")
    parser.add_argument("--fee", default=1, type=int, help="transaction fee")
    parser.add_argument("--nonce", default=None, type=int, help="optional sender nonce")
    parser.add_argument(
        "--mine-now",
        action="store_true",
        help="mine one block immediately after submission (default behavior)",
    )
    parser.add_argument(
        "--no-mine-now",
        action="store_false",
        dest="mine_now",
        help="do not mine immediately after submission",
    )
    parser.set_defaults(mine_now=True)


def _execute_action(args: argparse.Namespace) -> None:
    if args.action == "wallet_generate_key":
        _run_generate_key()
        return
    if args.action == "node_run":
        try:
            asyncio.run(_run_node_daemon(args))
        except KeyboardInterrupt:
            pass
        return
    if args.action == "node_stop":
        _run_node_stop(
            data_dir=Path(args.data_dir).expanduser(),
            timeout_seconds=args.timeout_seconds,
            force=args.force,
        )
        return
    if args.action == "tx_submit":
        data_dir = Path(args.data_dir).expanduser()
        if _daemon_running_for_data_dir(data_dir):
            _run_submit_transaction_via_network(
                data_dir=data_dir,
                host=str(args.host),
                port=int(args.port),
                private_key_hex=args.private_key,
                recipient=args.recipient,
                amount=args.amount,
                fee=args.fee,
                nonce=args.nonce,
                mine_now=args.mine_now,
            )
            return

    miner_address = args.miner_address
    if args.action == "tx_submit" and args.mine_now and miner_address is None:
        miner_address = _infer_sender_from_private_key(args.private_key)

    node = MiniChainNode(
        NodeConfig(
            data_dir=Path(args.data_dir),
            miner_address=miner_address,
        )
    )
    node.start()
    try:
        if args.action == "node_start":
            _print_heading("Node Status")
            _print_kv(
                {
                    "host": f"{args.host}:{args.port}",
                    "data_dir": str(Path(args.data_dir).expanduser()),
                    "chain_height": node.height,
                    "tip_hash": node.tip_hash,
                    "known_accounts": len(node.chain_manager.state.accounts),
                }
            )
            return

        if args.action == "wallet_balance":
            _run_balance(node=node, address=args.address)
            return

        if args.action == "wallet_details":
            _run_wallet_details(node=node, address=args.address)
            return

        if args.action == "wallet_list":
            _run_wallet_list(node=node, limit=args.limit)
            return

        if args.action == "chain_info":
            _run_chain_info(
                node=node,
                data_dir=Path(args.data_dir).expanduser(),
            )
            return

        if args.action == "chain_block":
            _run_block_query(node=node, height=args.height, block_hash=args.block_hash)
            return

        if args.action == "chain_accounts":
            _run_wallet_list(node=node, limit=args.limit)
            return

        if args.action == "tx_submit":
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

        if args.action == "mine":
            _run_mine(
                node=node,
                count=args.count,
                max_transactions=args.max_transactions,
            )
            return

        raise ValueError(f"Unsupported command action: {args.action}")
    finally:
        node.stop()


def _run_shell(parser: argparse.ArgumentParser, base_args: argparse.Namespace) -> None:
    print()
    print("+--------------------------------------+")
    print("|       MiniChain Interactive Shell     |")
    print("+--------------------------------------+")
    print("  Type 'help' for commands, 'exit' to quit.")
    print()
    while True:
        try:
            line = input("minichain >> ")
        except EOFError:
            print()
            break

        text = line.strip()
        if not text:
            continue
        if text in {"exit", "quit"}:
            print("  Goodbye.")
            break
        if text in {"help", "?"}:
            parser.print_help()
            continue

        try:
            tokens = _normalize_cli_tokens(shlex.split(text))
            shell_args = _shell_defaults(base_args) + tokens
            parsed = parser.parse_args(shell_args)
            if getattr(parsed, "action", None) == "shell":
                print("  [error] Nested shell is not supported.")
                continue
            if getattr(parsed, "action", None) is None:
                parsed.action = "node_start"
            _execute_action(parsed)
        except SystemExit:
            continue
        except Exception as exc:
            print(f"  [error] {exc}")


def _parse_peer_addresses(values: list[str]) -> tuple[PeerAddress, ...]:
    peers: list[PeerAddress] = []
    for value in values:
        peers.append(PeerAddress.from_string(value))
    return tuple(peers)


async def _run_node_daemon(args: argparse.Namespace) -> None:
    if args.mine and args.miner_address is None:
        raise ValueError("--miner-address is required when --mine is enabled")
    if args.mine_interval_seconds <= 0:
        raise ValueError("mine_interval_seconds must be positive")
    if args.status_interval_seconds <= 0:
        raise ValueError("status_interval_seconds must be positive")

    data_dir = Path(args.data_dir).expanduser()
    bootstrap_peers = _parse_peer_addresses(list(args.peer))
    node = MiniChainNode(
        NodeConfig(
            data_dir=data_dir,
            miner_address=args.miner_address,
        )
    )
    node.start()
    pid_file: Path | None = None
    network: MiniChainNetwork | None = None
    background_tasks: list[asyncio.Task[None]] = []
    try:
        pid_file = _acquire_node_pid_file(data_dir)

        network = MiniChainNetwork(
            NetworkConfig(
                host=args.host,
                port=args.port,
                advertise_host=args.advertise_host,
                node_id=args.node_id,
                bootstrap_peers=bootstrap_peers,
                enable_mdns=args.mdns,
                sync_batch_size=args.sync_batch_size,
            )
        )
        network.set_transaction_handler(lambda tx: _handle_network_transaction(node, tx))
        network.set_block_handler(lambda block: _handle_network_block(node, block))
        network.set_sync_handlers(
            get_height=lambda: node.height,
            get_block_by_height=node.chain_manager.get_canonical_block_by_height,
            apply_block=lambda block: _handle_network_block(node, block),
        )

        await network.start()
        _log(
            "info",
            "node_started "
            f"listen={network.listen_host}:{network.listen_port} "
            f"advertise={network.advertise_host}:{network.listen_port} "
            f"node_id={network.node_id} data_dir={data_dir}",
        )
        _log(
            "info",
            "consensus_policy=longest_valid_chain_wins "
            f"height={node.height} tip={node.tip_hash}",
        )
        _log(
            "info",
            "network_config "
            f"mdns={'on' if args.mdns else 'off'} "
            f"bootstrap_peers={','.join(args.peer) if args.peer else 'none'} "
            f"mining={'on' if args.mine else 'off'}",
        )

        background_tasks = [
            asyncio.create_task(
                _status_loop(
                    node=node,
                    network=network,
                    data_dir=data_dir,
                    interval_seconds=args.status_interval_seconds,
                )
            )
        ]
        if args.mine:
            background_tasks.append(
                asyncio.create_task(
                    _mining_loop(
                        node=node,
                        network=network,
                        interval_seconds=args.mine_interval_seconds,
                    )
                )
            )

        while True:
            await asyncio.sleep(3600)
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        if network is not None:
            await network.stop()
        node.stop()
        _release_node_pid_file(pid_file)
        _clear_runtime_status(data_dir)
        _log("info", "node_stopped")


async def _status_loop(
    *,
    node: MiniChainNode,
    network: MiniChainNetwork,
    data_dir: Path,
    interval_seconds: float,
) -> None:
    while True:
        connected = sorted(network.connected_peer_ids())
        _write_runtime_status(
            data_dir=data_dir,
            height=node.height,
            tip_hash=node.tip_hash,
            mempool_size=node.mempool.size(),
            connected_peers=len(connected),
        )
        _log(
            "status",
            f"height={node.height} tip={node.tip_hash} "
            f"mempool_size={node.mempool.size()} connected_peers={len(connected)}",
        )
        await asyncio.sleep(interval_seconds)


async def _mining_loop(
    *,
    node: MiniChainNode,
    network: MiniChainNetwork,
    interval_seconds: float,
) -> None:
    while True:
        try:
            block = node.mine_one_block()
            broadcast = await network.submit_block(block, already_applied=True)
            _log(
                "mine",
                f"mined_height={block.header.block_height} hash={block.hash().hex()} "
                f"broadcasted={'true' if broadcast else 'false'}",
            )
        except NodeError as exc:
            _log("warn", f"mining_error={exc}")
        await asyncio.sleep(interval_seconds)


def _handle_network_transaction(node: MiniChainNode, transaction: Transaction) -> bool:
    try:
        tx_id = node.submit_transaction(transaction)
    except NodeError as exc:
        _log("warn", f"tx_rejected error={exc}")
        return False
    _log("net", f"tx_accepted tx_id={tx_id}")
    return True


def _handle_network_block(node: MiniChainNode, block) -> bool:
    try:
        result = node.accept_block(block)
    except NodeError as exc:
        _log("warn", f"block_rejected error={exc}")
        return False

    accepted = result in {"extended", "reorg", "stored_fork", "duplicate"}
    if result in {"extended", "reorg"}:
        _log(
            "net",
            f"block_{result} height={node.height} "
            f"tip={node.tip_hash} hash={block.hash().hex()}",
        )
    elif result == "stored_fork":
        _log("net", f"block_stored_fork hash={block.hash().hex()}")
    return accepted


def _run_node_stop(*, data_dir: Path, timeout_seconds: float, force: bool) -> None:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    pid_file = _node_pid_path(data_dir)
    _print_heading("Node Stop")
    if not pid_file.exists():
        _print_kv(
            {
                "status": "not_running",
                "data_dir": str(data_dir),
                "pid_file": str(pid_file),
            }
        )
        return

    pid = _read_pid_file(pid_file)
    if pid is None:
        _release_node_pid_file(pid_file)
        _print_kv(
            {
                "status": "not_running",
                "reason": "invalid_pid_file_removed",
                "pid_file": str(pid_file),
            }
        )
        return

    if not _process_exists(pid):
        _release_node_pid_file(pid_file)
        _print_kv(
            {
                "status": "not_running",
                "reason": "stale_pid_file_removed",
                "stale_pid": pid,
            }
        )
        return

    graceful_signal = signal.SIGINT if os.name != "nt" else signal.SIGTERM
    try:
        os.kill(pid, graceful_signal)
    except ProcessLookupError:
        _release_node_pid_file(pid_file)
        _print_kv(
            {
                "status": "not_running",
                "reason": "process_exited_before_signal",
                "stale_pid": pid,
            }
        )
        return
    if _wait_for_exit(pid, timeout_seconds=timeout_seconds):
        _release_node_pid_file(pid_file)
        _print_kv(
            {
                "status": "stopped",
                "pid": pid,
                "signal": graceful_signal.name,
                "forced": "false",
            }
        )
        return

    if not force:
        raise ValueError(
            f"failed to stop pid {pid} within {timeout_seconds:.1f}s; retry with node stop --force"
        )
    if not hasattr(signal, "SIGKILL"):
        raise ValueError(
            f"failed to stop pid {pid} within {timeout_seconds:.1f}s; force stop unavailable"
        )

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        _release_node_pid_file(pid_file)
        _print_kv(
            {
                "status": "not_running",
                "reason": "process_exited_before_force_signal",
                "stale_pid": pid,
            }
        )
        return
    if _wait_for_exit(pid, timeout_seconds=2.0):
        _release_node_pid_file(pid_file)
        _print_kv(
            {
                "status": "stopped",
                "pid": pid,
                "signal": signal.SIGKILL.name,
                "forced": "true",
            }
        )
        return
    raise ValueError(f"failed to force-stop pid {pid}")


def _shell_defaults(base_args: argparse.Namespace) -> list[str]:
    defaults = [
        "--host",
        str(base_args.host),
        "--port",
        str(base_args.port),
        "--data-dir",
        str(base_args.data_dir),
    ]
    if base_args.miner_address is not None:
        defaults.extend(["--miner-address", str(base_args.miner_address)])
    return defaults


def _log(level: str, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    tag = level.upper().center(8)
    print(f"[{timestamp}] [{tag}] {message}")


def _acquire_node_pid_file(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _node_pid_path(data_dir)
    existing_pid = _read_pid_file(pid_file)
    if existing_pid is not None and _process_exists(existing_pid):
        raise ValueError(
            f"node already running for data_dir={data_dir} with pid={existing_pid}; "
            "use `minichain --data-dir <dir> node stop` first"
        )
    if pid_file.exists():
        pid_file.unlink()

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(pid_file), flags, 0o644)
    except FileExistsError as exc:
        race_pid = _read_pid_file(pid_file)
        if race_pid is not None and _process_exists(race_pid):
            raise ValueError(
                f"node already running for data_dir={data_dir} with pid={race_pid}; "
                "use `minichain --data-dir <dir> node stop` first"
            ) from exc
        raise ValueError(f"failed to acquire pid lock at {pid_file}") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()}\n")
    return pid_file


def _release_node_pid_file(pid_file: Path | None) -> None:
    if pid_file is None:
        return
    pid_file.unlink(missing_ok=True)


def _node_pid_path(data_dir: Path) -> Path:
    return data_dir / NODE_PID_FILENAME


def _node_runtime_status_path(data_dir: Path) -> Path:
    return data_dir / NODE_RUNTIME_STATUS_FILENAME


def _write_runtime_status(
    *,
    data_dir: Path,
    height: int,
    tip_hash: str,
    mempool_size: int,
    connected_peers: int,
) -> None:
    payload = {
        "updated_at": int(time.time()),
        "height": height,
        "tip_hash": tip_hash,
        "mempool_size": mempool_size,
        "connected_peers": connected_peers,
    }
    status_path = _node_runtime_status_path(data_dir)
    tmp_path = status_path.with_suffix(".tmp")
    with contextlib.suppress(OSError):
        tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp_path.replace(status_path)


def _read_runtime_status(data_dir: Path) -> dict[str, object] | None:
    status_path = _node_runtime_status_path(data_dir)
    if not status_path.exists():
        return None
    try:
        raw = status_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _clear_runtime_status(data_dir: Path) -> None:
    _node_runtime_status_path(data_dir).unlink(missing_ok=True)


def _daemon_running_for_data_dir(data_dir: Path) -> bool:
    pid_file = _node_pid_path(data_dir)
    pid = _read_pid_file(pid_file)
    if pid is None:
        return False
    return _process_exists(pid)


def _read_pid_file(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    content = pid_file.read_text(encoding="utf-8").strip()
    if not content:
        return None
    try:
        pid = int(content)
    except ValueError:
        return None
    if pid <= 0:
        return None
    return pid


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_exit(pid: int, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        time.sleep(0.05)
    return not _process_exists(pid)


def _normalize_cli_tokens(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None
    tokens = ["--help" if token == "-help" else token for token in argv]
    if not tokens:
        return tokens

    command_index = _find_command_index(tokens)
    if command_index < 0:
        return tokens

    command = tokens[command_index]
    legacy_map: dict[str, list[str]] = {
        "start": ["node", "start"],
        "generate-key": ["wallet", "generate-key"],
        "balance": ["wallet", "balance"],
        "submit-tx": ["tx", "submit"],
        "chain-info": ["chain", "info"],
        "block": ["chain", "block"],
        "mine-legacy": ["mine"],
    }
    replacement = legacy_map.get(command)
    if replacement is None:
        return tokens
    return tokens[:command_index] + replacement + tokens[command_index + 1 :]


def _find_command_index(tokens: list[str]) -> int:
    options_with_values = {"--host", "--port", "--data-dir", "--miner-address"}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in options_with_values:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return index
    return -1


def _run_generate_key() -> None:
    signing_key, verify_key = generate_key_pair()
    private_key = serialize_signing_key(signing_key)
    public_key = serialize_verify_key(verify_key)
    address = derive_address(verify_key)
    _print_heading("Wallet Key Material")
    _print_kv(
        {
            "private_key": private_key,
            "public_key": public_key,
            "address": address,
        }
    )


def _run_balance(*, node: MiniChainNode, address: str) -> None:
    if not _is_lower_hex(address, ADDRESS_HEX_LENGTH):
        raise ValueError("address must be a 20-byte lowercase hex string")
    account = node.chain_manager.state.get_account(address)
    _print_heading("Wallet Balance")
    _print_kv({"address": address, "balance": account.balance, "nonce": account.nonce})


def _run_wallet_details(*, node: MiniChainNode, address: str) -> None:
    if not _is_lower_hex(address, ADDRESS_HEX_LENGTH):
        raise ValueError("address must be a 20-byte lowercase hex string")
    exists = address in node.chain_manager.state.accounts
    account = node.chain_manager.state.get_account(address)
    _print_heading("Wallet Details")
    _print_kv(
        {
            "address": address,
            "exists": "true" if exists else "false",
            "balance": account.balance,
            "nonce": account.nonce,
        }
    )


def _run_wallet_list(*, node: MiniChainNode, limit: int) -> None:
    if limit <= 0:
        raise ValueError("limit must be positive")
    accounts = sorted(
        node.chain_manager.state.accounts.items(),
        key=lambda item: item[0],
    )
    _print_heading("Wallet Accounts")
    _print_kv(
        {
            "total_accounts": len(accounts),
            "showing": min(limit, len(accounts)),
        }
    )
    if accounts[:limit]:
        print(f"  {'#':>4}  {'Address':<42} {'Balance':>12}  {'Nonce':>6}")
        print(
            f"  {'----':>4}  {'------------------------------------------':<42} "
            f"{'------------':>12}  {'------':>6}"
        )
        for index, (address, account) in enumerate(accounts[:limit], start=1):
            print(f"  {index:>4}  {address:<42} {account.balance:>12}  {account.nonce:>6}")
        print()


def _run_chain_info(*, node: MiniChainNode, data_dir: Path) -> None:
    total_supply = sum(account.balance for account in node.chain_manager.state.accounts.values())
    connected_peers = 0
    if _daemon_running_for_data_dir(data_dir):
        runtime_status = _read_runtime_status(data_dir)
        if runtime_status is not None:
            value = runtime_status.get("connected_peers")
            if isinstance(value, int) and value >= 0:
                connected_peers = value
    _print_heading("Chain Info")
    _print_kv(
        {
            "height": node.height,
            "tip_hash": node.tip_hash,
            "accounts": len(node.chain_manager.state.accounts),
            "total_supply": total_supply,
            "connected_peers": connected_peers,
        }
    )


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
    _print_heading("Block")
    print(json.dumps(payload, sort_keys=True, indent=2))


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
    _print_heading("Transaction Submitted")
    _print_kv(
        {
            "submitted_tx_id": transaction_id,
            "sender": sender_address,
            "recipient": recipient,
            "amount": amount,
            "fee": fee,
            "nonce": resolved_nonce,
        }
    )

    if not mine_now:
        _print_kv({"queued_in_mempool": "true"})
        return

    mined_block = node.mine_one_block()
    _print_kv(
        {
            "mined_block_height": mined_block.header.block_height,
            "mined_block_hash": mined_block.hash().hex(),
        }
    )


def _run_submit_transaction_via_network(
    *,
    data_dir: Path,
    host: str,
    port: int,
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
    resolved_nonce = (
        _infer_sender_nonce_from_data_dir(data_dir=data_dir, sender_address=sender_address)
        if nonce is None
        else nonce
    )
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

    accepted, reason = asyncio.run(
        _submit_transaction_to_peer(
            transaction=transaction,
            host=host,
            port=port,
            timeout_seconds=3.0,
        )
    )
    if not accepted:
        detail = reason or "unknown rejection"
        raise ValueError(f"Transaction rejected by running node at {host}:{port}: {detail}")

    _print_heading("Transaction Submitted")
    _print_kv(
        {
            "submitted_tx_id": transaction.transaction_id().hex(),
            "sender": sender_address,
            "recipient": recipient,
            "amount": amount,
            "fee": fee,
            "nonce": resolved_nonce,
            "submitted_via": "network",
            "peer": f"{host}:{port}",
            "queued_in_mempool": "true",
        }
    )
    if mine_now:
        _print_kv({"note": "daemon mode active; block inclusion depends on node run --mine"})


def _infer_sender_nonce_from_data_dir(*, data_dir: Path, sender_address: str) -> int:
    node = MiniChainNode(NodeConfig(data_dir=data_dir))
    try:
        node.start()
        return node.chain_manager.state.get_account(sender_address).nonce
    except Exception as exc:
        raise ValueError(
            "unable to infer sender nonce from local state while daemon is running; "
            "pass --nonce explicitly"
        ) from exc
    finally:
        with contextlib.suppress(Exception):
            node.stop()


async def _submit_transaction_to_peer(
    *,
    transaction: Transaction,
    host: str,
    port: int,
    timeout_seconds: float,
) -> tuple[bool, str]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    peer = PeerAddress(host=host, port=port)
    peer.validate()

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=timeout_seconds,
    )
    try:
        await _write_json_line(
            writer,
            {
                "type": "hello",
                "node_id": f"cli-submit-{os.getpid()}-{int(time.time())}",
                "host": "127.0.0.1",
                "port": 0,
            },
        )
        await _wait_for_peer_hello(reader=reader, timeout_seconds=timeout_seconds)

        tx_id = transaction.transaction_id().hex()
        await _write_json_line(
            writer,
            {
                "type": "tx_gossip",
                "protocol": TX_GOSSIP_PROTOCOL_ID,
                "transaction_id": tx_id,
                "transaction": asdict(transaction),
            },
        )
        return await _wait_for_tx_result(
            reader=reader,
            expected_transaction_id=tx_id,
            timeout_seconds=timeout_seconds,
        )
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _wait_for_peer_hello(*, reader: asyncio.StreamReader, timeout_seconds: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        remaining = max(0.01, deadline - asyncio.get_running_loop().time())
        message = await _read_json_line(
            reader=reader,
            timeout_seconds=remaining,
            eof_ok=True,
        )
        if message is None:
            break
        if message.get("type") == "hello":
            return
    raise TimeoutError("timed out waiting for node handshake response")


async def _wait_for_tx_result(
    *,
    reader: asyncio.StreamReader,
    expected_transaction_id: str,
    timeout_seconds: float,
) -> tuple[bool, str]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        remaining = max(0.01, deadline - asyncio.get_running_loop().time())
        message = await _read_json_line(
            reader=reader,
            timeout_seconds=remaining,
            eof_ok=True,
        )
        if message is None:
            break
        if message.get("type") != "tx_result":
            continue
        transaction_id = message.get("transaction_id")
        if transaction_id != expected_transaction_id:
            continue
        accepted = bool(message.get("accepted"))
        reason = message.get("reason")
        if not isinstance(reason, str):
            reason = "unknown"
        return accepted, reason
    raise TimeoutError("timed out waiting for transaction result from node")


async def _read_json_line(
    *,
    reader: asyncio.StreamReader,
    timeout_seconds: float,
    eof_ok: bool,
) -> dict[str, object] | None:
    line = await asyncio.wait_for(reader.readline(), timeout=timeout_seconds)
    if not line:
        if eof_ok:
            return None
        raise ValueError("unexpected EOF while reading response")
    payload = json.loads(line.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("response payload must be a JSON object")
    return payload


async def _write_json_line(writer: asyncio.StreamWriter, payload: dict[str, object]) -> None:
    writer.write(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    await writer.drain()


def _run_mine(
    *,
    node: MiniChainNode,
    count: int,
    max_transactions: int | None,
) -> None:
    if count <= 0:
        raise ValueError("count must be positive")
    _print_heading("Mining")
    for index in range(1, count + 1):
        block = node.mine_one_block(max_transactions=max_transactions)
        print(
            f"  [{index}/{count}] "
            f"mined_block_{index}=height:{block.header.block_height},hash:{block.hash().hex()}"
        )
    print(f"\n  Done. Mined {count} block(s).")
    print()


def _infer_sender_from_private_key(private_key_hex: str) -> str:
    signing_key = deserialize_signing_key(private_key_hex)
    return derive_address(signing_key.verify_key)


def _is_lower_hex(value: str, expected_length: int) -> bool:
    if len(value) != expected_length:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def _print_heading(title: str) -> None:
    width = max(len(title) + 4, 40)
    border = "+" + "-" * (width - 2) + "+"
    print()
    print(border)
    print("| " + title.center(width - 4) + " |")
    print(border)


def _print_kv(values: dict[str, object]) -> None:
    if not values:
        return
    max_key_len = max(len(str(k)) for k in values)
    for key, value in values.items():
        print(f"  {str(key).ljust(max_key_len)} = {value}")
    print()


if __name__ == "__main__":
    main()
