# Stablecoin (ERC-20 style) Smart Contract Example
#
# This contract implements a minimal fungible token.
#
# Valid Payloads:
# - 'mint:<amount>'
# - 'transfer:<recipient_address>:<amount>'

if msg['data'].startswith('mint:'):
    # In a real contract, you would restrict this to an owner address!
    # For this example, anyone can mint tokens to themselves.
    amount = int(msg['data'].split(':')[1])
    if amount <= 0:
        raise Exception("Amount must be positive")
    
    sender = msg['sender']
    storage[sender] = storage.get(sender, 0) + amount
    storage['total_supply'] = storage.get('total_supply', 0) + amount

elif msg['data'].startswith('transfer:'):
    parts = msg['data'].split(':')
    if len(parts) != 3:
        raise Exception("Invalid transfer format")
    
    to_address = parts[1]
    amount = int(parts[2])
    
    if amount <= 0:
        raise Exception("Amount must be positive")
    
    sender = msg['sender']
    sender_balance = storage.get(sender, 0)
    
    if sender_balance >= amount:
        storage[sender] -= amount
        storage[to_address] = storage.get(to_address, 0) + amount
    else:
        raise Exception("Insufficient token balance")

else:
    raise Exception("Unknown command. Valid commands: mint:<amount>, transfer:<to>:<amount>")
