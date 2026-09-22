"""
tests/test_contract_unit.py

Direct unit tests for minichain.contract, targeting code paths that the
existing end-to-end tests (test_contract.py, test_contract_calls.py) don't
get coverage credit for: _safe_exec_worker runs inside a real
multiprocessing.Process there, and coverage.py doesn't trace child
processes by default. Calling it directly, in-process, with a manually
created Pipe gets real line coverage for the sandbox's actual execution
logic (gas metering, transfer_out validation, error handling), plus the
ContractMachine.execute() and _validate_code_ast() branches that don't
require a subprocess at all.
"""

import sys
import multiprocessing
from unittest.mock import patch

import pytest

from minichain.contract import ContractMachine, GasMeter, OutOfGasException, _safe_exec_worker
from minichain.state import State


# ------------------------------------------------------------------
# _safe_exec_worker (in-process, bypassing multiprocessing.Process)
#
# _safe_exec_worker calls sys.settrace(meter.trace_calls) then
# sys.settrace(None) when it's done. In a real deployment that's fine --
# it runs in a fresh subprocess. Called directly in-process for unit
# testing, that sys.settrace(None) silently rips out coverage.py's own
# tracer for the rest of the test session. Every direct call is wrapped
# to save and restore whatever tracer was active beforehand.
# ------------------------------------------------------------------

def _run_worker(code, gas_limit=100_000, context=None):
    parent_conn, child_conn = multiprocessing.Pipe()
    globals_dict = {"__builtins__": {"True": True, "False": False, "range": range, "int": int, "Exception": Exception}}
    context_dict = context if context is not None else {"storage": {}}
    outer_trace = sys.gettrace()
    try:
        _safe_exec_worker(code, globals_dict, context_dict, child_conn, gas_limit)
    finally:
        sys.settrace(outer_trace)
    return parent_conn.recv()


def test_worker_success_updates_storage():
    msg = _run_worker("storage['x'] = 1 + 1")
    assert msg["type"] == "return"
    assert msg["status"] == "success"
    assert msg["storage"]["x"] == 2
    assert msg["gas_used"] > 0


def test_worker_out_of_gas():
    msg = _run_worker("i = 0\nwhile True:\n    i += 1", gas_limit=50)
    assert msg["status"] == "error"
    assert msg["error"] == "Out of gas!"
    assert msg["gas_used"] == 50


def test_worker_runtime_exception_reports_partial_gas():
    msg = _run_worker("raise Exception('boom')")
    assert msg["status"] == "error"
    assert "boom" in msg["error"]
    assert msg["gas_used"] >= 0


def test_worker_transfer_out_valid():
    code = "transfer_out('ab' * 20, 5)"
    msg = _run_worker(code)
    assert msg["status"] == "success"
    assert msg["transfers"] == [{"to": "ab" * 20, "amount": 5}]


@pytest.mark.parametrize("bad_call", [
    "transfer_out('ab' * 20, 0)",          # amount not positive
    "transfer_out('ab' * 20, -5)",         # amount negative
    "transfer_out('zz' * 20, 5)",          # not valid hex
    "transfer_out('ab', 5)",               # wrong length
])
def test_worker_transfer_out_rejects_invalid_input(bad_call):
    msg = _run_worker(bad_call)
    assert msg["status"] == "error"
    assert "Invalid" in msg["error"]


def test_worker_call_contract_sends_request_and_uses_reply():
    parent_conn, child_conn = multiprocessing.Pipe()
    # Pre-queue the reply call_contract() will block on, since this test
    # drives both ends of the pipe from the same thread.
    parent_conn.send({"success": True, "result": "pong"})

    code = "storage['reply'] = call_contract('cd' * 20, 'ping')"
    globals_dict = {"__builtins__": {}}
    context_dict = {"storage": {}}
    outer_trace = sys.gettrace()
    try:
        _safe_exec_worker(code, globals_dict, context_dict, child_conn, gas_limit=100_000)
    finally:
        sys.settrace(outer_trace)

    call_msg = parent_conn.recv()
    assert call_msg["type"] == "call"
    assert call_msg["address"] == "cd" * 20
    assert call_msg["payload"] == "ping"

    return_msg = parent_conn.recv()
    assert return_msg["status"] == "success"
    assert return_msg["storage"]["reply"] == "pong"


def test_worker_call_contract_failure_raises_in_contract_code():
    parent_conn, child_conn = multiprocessing.Pipe()
    parent_conn.send({"success": False, "error": "receiver reverted"})

    code = "call_contract('cd' * 20, 'ping')"
    globals_dict = {"__builtins__": {}}
    context_dict = {"storage": {}}
    outer_trace = sys.gettrace()
    try:
        _safe_exec_worker(code, globals_dict, context_dict, child_conn, gas_limit=100_000)
    finally:
        sys.settrace(outer_trace)

    parent_conn.recv()  # the "call" request
    return_msg = parent_conn.recv()
    assert return_msg["status"] == "error"
    assert "receiver reverted" in return_msg["error"]


# ------------------------------------------------------------------
# GasMeter
# ------------------------------------------------------------------

def test_gas_meter_raises_when_exhausted():
    meter = GasMeter(limit=2)

    class FakeFrame:
        f_trace_opcodes = False

    fake_frame = FakeFrame()
    meter.trace_calls(fake_frame, 'opcode', None)  # gas: 2 -> 1, no raise yet
    assert fake_frame.f_trace_opcodes is True
    with pytest.raises(OutOfGasException):
        meter.trace_calls(fake_frame, 'opcode', None)  # gas: 1 -> 0, raises


# ------------------------------------------------------------------
# ContractMachine.execute() branches that don't need a subprocess
# ------------------------------------------------------------------

@pytest.fixture
def state():
    return State()


def test_execute_max_call_depth_exceeded(state):
    machine = ContractMachine(state)
    from minichain.network_config import MAX_CALL_DEPTH
    result = machine.execute("addr", "sender", "payload", 0, 1000, depth=MAX_CALL_DEPTH + 1)
    assert result["success"] is False
    assert "Max call depth exceeded" in result["error"]


def test_execute_account_not_found(state):
    machine = ContractMachine(state)
    # get_account auto-creates a blank account, so "not found" only triggers
    # for a falsy return -- patch it to simulate that.
    with patch.object(state, "get_account", return_value=None):
        result = machine.execute("nowhere", "sender", "payload", 0, 1000)
    assert result["success"] is False
    assert "Account not found" in result["error"]


def test_execute_no_code(state):
    machine = ContractMachine(state)
    state.get_account("plain-account")  # materialize a blank (codeless) account
    result = machine.execute("plain-account", "sender", "payload", 0, 1000)
    assert result["success"] is False
    assert "No code" in result["error"]


def test_execute_ast_validation_failure(state):
    machine = ContractMachine(state)
    account = state.get_account("malicious")
    account["code"] = "x.__class__"  # double-underscore attribute access
    result = machine.execute("malicious", "sender", "payload", 0, 1000)
    assert result["success"] is False
    assert "AST Validation Failed" in result["error"]


def test_execute_non_json_serializable_storage_fails(state):
    """A real end-to-end run (real subprocess) whose storage ends up holding a
    value json.dumps can't handle -- these lines run in the parent process
    (inside execute(), after the worker returns), so they're reachable
    without the sys.settrace conflict that limits _safe_exec_worker itself."""
    machine = ContractMachine(state)
    account = state.get_account("bad-storage")
    account["code"] = "storage['x'] = 1j"  # a complex literal; json can't serialize it
    result = machine.execute("bad-storage", "sender", "payload", 0, gas_limit=100_000)
    assert result["success"] is False
    assert "not JSON serializable" in result["error"]


def test_execute_storage_size_exceeds_gas_limit(state):
    """Cheap to execute, but the resulting storage is big enough that
    storage_gas alone pushes total_gas past gas_limit."""
    machine = ContractMachine(state)
    account = state.get_account("big-storage")
    long_literal = "a" * 300
    account["code"] = f"storage['x'] = '{long_literal}'"
    result = machine.execute("big-storage", "sender", "payload", 0, gas_limit=200)
    assert result["success"] is False
    assert "Storage size exceeded limit" in result["error"]


def test_execute_subprocess_crash_is_reported(state):
    """If multiprocessing.Process itself blows up before running any contract
    code, execute() must still return a clean failure, not propagate."""
    machine = ContractMachine(state)
    account = state.get_account("crasher")
    account["code"] = "storage['x'] = 1"

    with patch("minichain.contract.multiprocessing.Process", side_effect=RuntimeError("boom")):
        result = machine.execute("crasher", "sender", "payload", 0, 1000)

    assert result["success"] is False
    assert result["error"] == "System Error"


# ------------------------------------------------------------------
# _validate_code_ast
# ------------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "x.__class__",
    "__builtins__",
    "import os",
    "from os import path",
    "type(1)",
    "getattr(storage, 'x')",
    "setattr(storage, 'x', 1)",
    "delattr(storage, 'x')",
    "x = '__secret__'",
    "x = f'{1}'",
    "x = 2 ** 10",
    "x = 2 * 10",
    "x = a @ b",
])
def test_validate_code_ast_rejects_unsafe_code(state, code):
    machine = ContractMachine(state)
    assert machine._validate_code_ast(code) is False


def test_validate_code_ast_rejects_syntax_error(state):
    machine = ContractMachine(state)
    assert machine._validate_code_ast("def broken(:") is False


def test_validate_code_ast_accepts_safe_code(state):
    machine = ContractMachine(state)
    assert machine._validate_code_ast("storage['x'] = 1 + 1\nif storage['x'] > 0:\n    storage['y'] = 2") is True
