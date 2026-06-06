# MiniSwap (DEX) Smart Contract Example
#
# This contract implements a minimal Automated Market Maker (AMM) 
# using the x * y = k constant product formula.
# It trades the native MiniChain coin (msg['value']) against a minted DEX Token.
#
# Valid Payloads:
# - 'init' (Must send initial native coins to provide liquidity)
# - 'buy'  (Sends native coins, receives DEX tokens)
# - 'sell:<amount>' (Sells DEX tokens, receives native coins)
#
# Note: Since native coins sent to the contract are automatically added to the 
# contract's balance by the state manager BEFORE execution, msg['value'] is already
# inside the contract's physical balance.

if msg['data'] == 'init':
    # Initialize the liquidity pool
    if storage.get('k') is not None:
        raise Exception("Already initialized")
    if msg['value'] <= 0:
        raise Exception("Must provide initial native coin liquidity")
    
    # We will arbitrarily mint 1000 DEX tokens to match the initial coin liquidity
    storage['native_reserve'] = msg['value']
    storage['token_reserve'] = 1000
    storage['k'] = storage['native_reserve'] * storage['token_reserve']
    
    # Give the initial tokens to the creator
    storage[msg['sender']] = 1000

elif msg['data'] == 'buy':
    # User sends native coins to buy DEX tokens
    if storage.get('k') is None:
        raise Exception("Not initialized")
    if msg['value'] <= 0:
        raise Exception("Must send coins to buy tokens")
    
    # Calculate how many tokens to give using x * y = k
    # (native_reserve + msg['value']) * (token_reserve - tokens_out) = k
    new_native_reserve = storage['native_reserve'] + msg['value']
    new_token_reserve = storage['k'] // new_native_reserve
    
    tokens_out = storage['token_reserve'] - new_token_reserve
    if tokens_out <= 0:
        raise Exception("Not enough tokens to dispense")
    
    # Update reserves
    storage['native_reserve'] = new_native_reserve
    storage['token_reserve'] = new_token_reserve
    
    # Credit tokens to buyer
    sender = msg['sender']
    storage[sender] = storage.get(sender, 0) + tokens_out

elif msg['data'].startswith('sell:'):
    # User sells DEX tokens to get native coins back
    if storage.get('k') is None:
        raise Exception("Not initialized")
        
    parts = msg['data'].split(':')
    tokens_to_sell = int(parts[1])
    
    sender = msg['sender']
    sender_tokens = storage.get(sender, 0)
    if sender_tokens < tokens_to_sell:
        raise Exception("Insufficient token balance")
    
    # Deduct tokens from user
    storage[sender] -= tokens_to_sell
    
    # Calculate how many native coins to give using x * y = k
    # (token_reserve + tokens_to_sell) * (native_reserve - coins_out) = k
    new_token_reserve = storage['token_reserve'] + tokens_to_sell
    new_native_reserve = storage['k'] // new_token_reserve
    
    coins_out = storage['native_reserve'] - new_native_reserve
    if coins_out <= 0:
        raise Exception("Not enough coins to dispense")
        
    # Update reserves
    storage['native_reserve'] = new_native_reserve
    storage['token_reserve'] = new_token_reserve
    
    # Wait! In MiniChain, smart contracts cannot arbitrarily initiate outgoing transactions yet.
    # To properly implement 'sell', the contract engine would need a 'transfer_out' API.
    # For now, we will just record their native coin balance in storage.
    storage[f"{sender}_native_credit"] = storage.get(f"{sender}_native_credit", 0) + coins_out

else:
    raise Exception("Unknown command.")
