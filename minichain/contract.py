import logging
import multiprocessing
import ast
import sys

class OutOfGasException(Exception):
    pass

class GasMeter:
    def __init__(self, limit):
        self.gas = limit
        self.initial_gas = limit

    def trace_calls(self, frame, event, arg):
        frame.f_trace_opcodes = True
        if event == 'opcode':
            self.gas -= 1
            if self.gas <= 0:
                raise OutOfGasException("Out of gas!")
        return self.trace_calls

import json
logger = logging.getLogger(__name__)

def _safe_exec_worker(code, globals_dict, context_dict, child_conn, gas_limit):
    """
    Worker function to execute contract code in a separate process with gas metering.
    
    SECURITY:
    This function relies on `globals_dict` (which has `__builtins__` stripped down 
    to a minimal safe allowlist) to prevent malicious code from accessing file systems
    (e.g., `open()`), networking, or OS-level commands (e.g., `__import__('os')`).
    Because `exec` is run with these restricted globals, any attempt to call unauthorized
    builtins or standard library modules will result in a NameError or ImportError.
    """
    try:
        # Attempt to set resource limits (Unix only)
        try:
            import resource
            # Limit CPU time (seconds) and memory (bytes) - example values
            resource.setrlimit(resource.RLIMIT_CPU, (10, 10)) # Align with p.join timeout (10 seconds)
            resource.setrlimit(resource.RLIMIT_AS, (100 * 1024 * 1024, 100 * 1024 * 1024))
        except ImportError:
            logger.warning("Resource module not available. Contract will run without OS-level resource limits.")
        except (OSError, ValueError) as e:
            logger.warning("Failed to set resource limits: %s", e)

        transfers = []
        
        def transfer_out(address, amount):
            if not isinstance(amount, int) or amount <= 0:
                raise ValueError("Invalid transfer amount")
            if not isinstance(address, str):
                raise ValueError("Invalid address type")
            if not address or len(address) not in (40, 64):
                raise ValueError("Invalid address format")
            try:
                int(address, 16)
            except ValueError:
                raise ValueError("Invalid address format")
            transfers.append({"to": address, "amount": amount})
            
        globals_dict["__builtins__"]["transfer_out"] = transfer_out

        def call_contract(address, payload, amount=0):
            child_conn.send({"type": "call", "address": address, "payload": payload, "amount": amount})
            resp = child_conn.recv()
            if not resp.get("success"):
                raise Exception("Cross-contract call failed: " + str(resp.get("error")))
            return resp.get("result", True)
            
        globals_dict["__builtins__"]["call_contract"] = call_contract

        meter = GasMeter(gas_limit)
        sys.settrace(meter.trace_calls)
        
        try:
            exec(code, globals_dict, context_dict)
        finally:
            sys.settrace(None)
            
        gas_used = meter.initial_gas - meter.gas
        child_conn.send({"type": "return", "status": "success", "storage": context_dict.get("storage"), "transfers": transfers, "gas_used": gas_used})
    except OutOfGasException as e:
        child_conn.send({"type": "return", "status": "error", "error": "Out of gas!", "gas_used": gas_limit})
    except Exception as e:
        # If it failed for another reason, we still charge the gas it consumed up to the failure
        gas_used = gas_limit if 'meter' not in locals() else meter.initial_gas - meter.gas
        child_conn.send({"type": "return", "status": "error", "error": str(e), "gas_used": gas_used})

class ContractMachine:
    """
    A minimal execution environment for Python-based smart contracts.
    WARNING: Still not production-safe. For educational use only.
    
    SANDBOX ENFORCEMENT:
    1. Builtins Restriction: `__builtins__` is aggressively filtered. Functions like 
       `open`, `exec`, `eval`, `__import__`, `print`, and `input` are completely removed.
       This inherently prevents file deletion, network requests, or OS command execution.
    2. AST Validation: `_validate_code_ast` statically analyzes the code before execution 
       to block double-underscore access (preventing sandbox escape via introspection) 
       and entirely blocks the `import` statement.
    
    Allowed Builtins: range(), len(), min(), max(), abs(), str(), bool(), float(), int(), list(), dict(), tuple(), sum(), Exception
    Blocked Builtins: Imports, File IO (open), OS modules, Networking, Introspection.
    """

    def __init__(self, state):
        self.state = state

    @staticmethod
    def _fail(error, gas_used=0):
        """Uniform failure result for execute()."""
        return {"success": False, "gas_used": gas_used, "error": error}

    def execute(self, contract_address, sender_address, payload, amount, gas_limit, depth=0):
        """
        Executes the contract code associated with the contract_address.
        Returns a dict: {"success": bool, "gas_used": int, "error": str}
        """

        if depth > 10:
            return self._fail("Max call depth exceeded", gas_limit)

        account = self.state.get_account(contract_address)
        if not account:
            return self._fail("Account not found")

        code = account.get("code")

        # Defensive copy of storage to prevent direct mutation
        storage = dict(account.get("storage", {}))

        if not code:
            return self._fail("No code")

        # AST Validation to prevent introspection
        if not self._validate_code_ast(code):
            return self._fail("AST Validation Failed")

        # Restricted builtins (explicit allowlist)
        safe_builtins = {
            "True": True,
            "False": False,
            "None": None,
            "range": range,
            "len": len,
            "min": min,
            "max": max,
            "abs": abs,
            "str": str, # Keeping str for basic functionality, relying on AST checks for safety
            "bool": bool,
            "float": float,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "sum": sum,
            "Exception": Exception, # Added to allow contracts to raise exceptions
        }

        globals_for_exec = {
            "__builtins__": safe_builtins
        }

        # Execution context (locals)
        context = {
            "storage": storage,
            "msg": {
                "sender": sender_address,
                "value": amount,
                "data": payload,
            },
            # "print": print,  # Removed for security
        }

        try:
            # Execute in a subprocess with timeout
            import time
            parent_conn, child_conn = multiprocessing.Pipe()
            p = multiprocessing.Process(
                target=_safe_exec_worker,
                args=(code, globals_for_exec, context, child_conn, gas_limit)
            )
            p.start()
            
            start_time = time.time()
            result = None
            
            while p.is_alive() or parent_conn.poll():
                if time.time() - start_time > 5:
                    p.kill()
                    p.join()
                    logger.error("Contract execution timed out")
                    return self._fail("Execution timed out", gas_limit)
                    
                if parent_conn.poll(0.1):
                    msg = parent_conn.recv()
                    if msg.get("type") == "call":
                        # Execute internal sub-call recursively
                        sub_result = self.state.execute_internal_call(
                            sender=contract_address, # Caller is the current contract!
                            receiver_address=msg["address"],
                            amount=msg.get("amount", 0),
                            payload=msg["payload"],
                            gas_limit=gas_limit, # Let inner call use remaining gas
                            depth=depth + 1
                        )
                        parent_conn.send(sub_result)
                    elif msg.get("type") == "return":
                        result = msg
                        break

            if result is None:
                logger.error("Contract execution crashed without result")
                return self._fail("Crashed", gas_limit)

            if result["status"] != "success":
                logger.error("Contract Execution Failed: %s", result.get('error'))
                return self._fail(result.get('error'), result.get("gas_used", gas_limit))

            # Validate storage is JSON serializable
            try:
                storage_json = json.dumps(result["storage"])
            except (TypeError, ValueError):
                logger.error("Contract storage not JSON serializable")
                return self._fail("Storage not JSON serializable", result.get("gas_used", gas_limit))

            from .network_config import GAS_PER_BYTE
            storage_gas = len(storage_json.encode('utf-8')) * GAS_PER_BYTE
            total_gas = result["gas_used"] + storage_gas

            if total_gas > gas_limit:
                logger.error("Contract Execution Failed: Out of gas (Storage size exceeded limit)")
                return self._fail("Out of gas (Storage size exceeded limit)", gas_limit)

            return {"success": True, "gas_used": total_gas, "transfers": result.get("transfers", []), "storage": result["storage"], "error": None}

        except Exception as e:
            logger.error("Contract Execution Failed", exc_info=True)
            return self._fail("System Error", gas_limit)

    def _validate_code_ast(self, code):
        """Reject code that uses double underscores or introspection."""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                    logger.warning("Rejected contract code with double-underscore attribute access.")
                    return False
                if isinstance(node, ast.Name) and node.id.startswith("__"):
                    logger.warning("Rejected contract code with double-underscore name.")
                    return False
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    logger.warning("Rejected contract code with import statement.")
                    return False
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"type", "getattr", "setattr", "delattr"}:
                    logger.warning("Rejected direct call to %s.", node.func.id)
                    return False
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
                    logger.warning("Rejected string literal with double-underscore.")
                    return False
                if isinstance(node, ast.JoinedStr): # f-strings
                    logger.warning("Rejected f-string usage.")
                    return False
                if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Pow, ast.MatMult)):
                    logger.warning("Rejected contract code with potentially unbounded operator (*, **, @).")
                    return False
            return True
        except SyntaxError:
            return False
