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

import json # Moved to module-level import
logger = logging.getLogger(__name__)

def _safe_exec_worker(code, globals_dict, context_dict, result_queue, gas_limit):
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
            resource.setrlimit(resource.RLIMIT_CPU, (2, 2)) # Align with p.join timeout (2 seconds)
            resource.setrlimit(resource.RLIMIT_AS, (100 * 1024 * 1024, 100 * 1024 * 1024))
        except ImportError:
            logger.warning("Resource module not available. Contract will run without OS-level resource limits.")
        except (OSError, ValueError) as e:
            logger.warning("Failed to set resource limits: %s", e)

        meter = GasMeter(gas_limit)
        sys.settrace(meter.trace_calls)
        
        try:
            exec(code, globals_dict, context_dict)
        finally:
            sys.settrace(None)
            
        gas_used = meter.initial_gas - meter.gas
        result_queue.put({"status": "success", "storage": context_dict.get("storage"), "gas_used": gas_used})
    except OutOfGasException as e:
        result_queue.put({"status": "error", "error": "Out of gas!", "gas_used": gas_limit})
    except Exception as e:
        # If it failed for another reason, we still charge the gas it consumed up to the failure
        gas_used = gas_limit if 'meter' not in locals() else meter.initial_gas - meter.gas
        result_queue.put({"status": "error", "error": str(e), "gas_used": gas_used})

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

    def execute(self, contract_address, sender_address, payload, amount, gas_limit):
        """
        Executes the contract code associated with the contract_address.
        Returns a dict: {"success": bool, "gas_used": int, "error": str}
        """

        account = self.state.get_account(contract_address)
        if not account:
            return {"success": False, "gas_used": 0, "error": "Account not found"}

        code = account.get("code")

        # Defensive copy of storage to prevent direct mutation
        storage = dict(account.get("storage", {}))

        if not code:
            return {"success": False, "gas_used": 0, "error": "No code"}

        # AST Validation to prevent introspection
        if not self._validate_code_ast(code):
            return {"success": False, "gas_used": 0, "error": "AST Validation Failed"}

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
            queue = multiprocessing.Queue()
            p = multiprocessing.Process(
                target=_safe_exec_worker,
                args=(code, globals_for_exec, context, queue, gas_limit)
            )
            p.start()
            p.join(timeout=2)  # 2 second timeout

            if p.is_alive():
                p.kill()
                p.join()
                logger.error("Contract execution timed out")
                return {"success": False, "gas_used": gas_limit, "error": "Execution timed out"}

            try:
                result = queue.get(timeout=1)
            except Exception:
                logger.error("Contract execution crashed without result")
                return {"success": False, "gas_used": gas_limit, "error": "Crashed"}
            
            if result["status"] != "success":
                logger.error("Contract Execution Failed: %s", result.get('error'))
                return {"success": False, "gas_used": result.get("gas_used", gas_limit), "error": result.get('error')}

            # Validate storage is JSON serializable
            try:
                json.dumps(result["storage"])
            except (TypeError, ValueError):
                logger.error("Contract storage not JSON serializable")
                return {"success": False, "gas_used": result.get("gas_used", gas_limit), "error": "Storage not JSON serializable"}

            # Commit updated storage only after successful execution
            self.state.update_contract_storage(
                contract_address,
                result["storage"]
            )

            return {"success": True, "gas_used": result["gas_used"], "error": None}

        except Exception as e:
            logger.error("Contract Execution Failed", exc_info=True)
            return {"success": False, "gas_used": gas_limit, "error": "System Error"}

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
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == 'type':
                        logger.warning("Rejected type() call.")
                        return False
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"getattr", "setattr", "delattr"}:
                    logger.warning("Rejected direct call to %s.", node.func.id)
                    return False
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "__" in node.value:
                        logger.warning("Rejected string literal with double-underscore.")
                        return False
                if isinstance(node, ast.JoinedStr): # f-strings
                    logger.warning("Rejected f-string usage.")
                    return False
            return True
        except SyntaxError:
            return False
