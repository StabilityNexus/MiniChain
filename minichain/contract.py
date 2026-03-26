import logging
import multiprocessing
import ast
import json

logger = logging.getLogger(__name__)

def _safe_exec_worker(code, globals_dict, context_dict, result_queue):
    try:
        exec(code, globals_dict, context_dict)
        result_queue.put({"status": "success", "storage": context_dict.get("storage")})
    except Exception as e:
        result_queue.put({"status": "error", "error": str(e)})

class ContractMachine:
    def __init__(self, state):
        self.state = state

    def execute(self, contract_address, sender_address, payload, amount):
        account = self.state.get_account(contract_address)
        if not account or not account.get("code"):
            return False

        code = account["code"]
        storage = dict(account.get("storage", {}))

        if not self._validate_code_ast(code):
            return False

        # Minimal & Deterministic builtins (No floats)
        safe_builtins = {
            "True": True, "False": False, "None": None,
            "range": range, "len": len, "str": str, "int": int,
            "bool": bool, "list": list, "dict": dict, "Exception": Exception
        }

        context = {
            "storage": storage,
            "msg": {"sender": sender_address, "value": amount, "data": payload}
        }

        try:
            queue = multiprocessing.Queue()
            p = multiprocessing.Process(
                target=_safe_exec_worker,
                args=(code, {"__builtins__": safe_builtins}, context, queue)
            )
            p.start()
            p.join(timeout=2)

            if p.is_alive():
                p.kill()
                p.join()
                logger.error("Contract timeout")
                return False

            result = queue.get(timeout=1)
            if result["status"] != "success":
                return False

            json.dumps(result["storage"]) # Validate JSON serializability
            self.state.update_contract_storage(contract_address, result["storage"])
            return True

        except Exception:
            logger.error("Contract Execution Failed", exc_info=True)
            return False

    def _validate_code_ast(self, code):
        try:
            for node in ast.walk(ast.parse(code)):
                if isinstance(node, (ast.Import, ast.ImportFrom)): return False
                if isinstance(node, ast.Attribute) and node.attr.startswith("__"): return False
                if isinstance(node, ast.Name) and node.id.startswith("__"): return False
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"type", "getattr", "setattr", "delattr"}: return False
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value: return False
                if isinstance(node, ast.JoinedStr): return False
            return True
        except SyntaxError:
            return False