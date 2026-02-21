"""Scaffolding checks for Issue #1."""

from __future__ import annotations

import importlib

COMPONENT_MODULES = [
    "crypto",
    "transaction",
    "block",
    "state",
    "mempool",
    "consensus",
    "network",
    "storage",
    "node",
    "serialization",
    "merkle",
    "genesis",
]


def test_component_modules_are_importable() -> None:
    for module in COMPONENT_MODULES:
        imported = importlib.import_module(f"minichain.{module}")
        assert imported is not None


def test_cli_parser_defaults() -> None:
    from minichain.__main__ import build_parser

    args = build_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 7000
