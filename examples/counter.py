# Counter Smart Contract Example
#
# This is a simple counter contract designed to demonstrate the basic
# structure of smart contracts in MiniChain.
#
# Available built-ins in the MiniChain Sandbox:
# - `storage`: A dictionary persisting state across executions.
# - `msg`: A dictionary containing transaction info:
#    - `msg['sender']` : The address of the caller.
#    - `msg['value']`  : The amount of coins attached to the call.
#    - `msg['data']`   : The payload string.
#
# Available functions: range(), len(), min(), max(), abs(), str(), bool(), float(), int(), list(), dict(), tuple(), sum()
#
# NOTE: The sandbox does NOT allow imports, print(), or any double-underscore methods.

if msg["data"] == "increment":
    # Retrieve the current counter value, defaulting to 0 if it doesn't exist
    current_value = storage.get("counter", 0)

    # Increment the counter
    storage["counter"] = current_value + 1

elif msg["data"] == "decrement":
    current_value = storage.get("counter", 0)
    storage["counter"] = current_value - 1

elif msg["data"] == "reset":
    # You can restrict who can reset the counter by checking the sender!
    # (Just an example, anyone can call this one)
    storage["counter"] = 0

else:
    # If the payload doesn't match any known command, raise an exception.
    # This will fail the transaction and refund the 'amount' to the sender,
    # but the network will keep the 'fee' as gas.
    raise Exception("Unknown command. Valid commands: increment, decrement, reset")
