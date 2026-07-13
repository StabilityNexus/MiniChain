# =============================================================================
# MiniChain Storage Paradigms Example: Grouped Values vs Composite Keys
# =============================================================================
# 
# Problem Statement:
# Unlike Solidity, which allows multiple distinct `mapping` variables,
# MiniChain provides a single global `storage` dictionary per contract.
# If you naively try to save an age and a name to the same address key:
#     storage[sender] = age
#     storage[sender] = name   <-- This overwrites the age!
#
# This contract demonstrates two paradigms to solve this key collision.
#
# Valid Payloads:
# - 'set_grouped:<age>:<name>'
# - 'update_age_grouped:<new_age>'
#
# - 'set_composite:<age>:<name>'
# - 'update_age_composite:<new_age>'
# =============================================================================

payload = msg['data']
sender = msg['sender']

# -----------------------------------------------------------------------------
# Paradigm 1: Grouped Values
# -----------------------------------------------------------------------------
# We store a Python dictionary as the value for the sender's address.
# Pros: Keeps all user data neatly organized in one object.
# Cons: To update a single field, we must read the whole dict and write it back.

if payload.startswith('set_grouped:'):
    parts = payload.split(':')
    if len(parts) != 3:
        raise Exception("Invalid format. Use set_grouped:<age>:<name>")
        
    age = int(parts[1])
    name = parts[2]
    
    # Store the entire profile as a single dictionary
    storage[sender] = {
        "age": age,
        "name": name
    }

elif payload.startswith('update_age_grouped:'):
    parts = payload.split(':')
    if len(parts) != 2:
        raise Exception("Invalid format. Use update_age_grouped:<new_age>")
        
    new_age = int(parts[1])
    
    # Notice the inefficiency here: we must load the entire dict,
    # modify one field, and then re-save the entire dict to storage.
    profile = storage.get(sender, {})
    if not profile:
        raise Exception("Profile not found")
        
    profile["age"] = new_age
    storage[sender] = profile  # Re-saving the whole dictionary

# -----------------------------------------------------------------------------
# Paradigm 2: Composite Keys
# -----------------------------------------------------------------------------
# We namespace the keys using a string prefix (e.g., "age:0x123").
# Pros: We can update individual fields independently without fetching everything.
# Cons: Data is slightly fragmented across the storage dictionary.

elif payload.startswith('set_composite:'):
    parts = payload.split(':')
    if len(parts) != 3:
        raise Exception("Invalid format. Use set_composite:<age>:<name>")
        
    age = int(parts[1])
    name = parts[2]
    
    # Store each field independently using namespaced keys
    storage[f"age:{sender}"] = age
    storage[f"name:{sender}"] = name

elif payload.startswith('update_age_composite:'):
    parts = payload.split(':')
    if len(parts) != 2:
        raise Exception("Invalid format. Use update_age_composite:<new_age>")
        
    new_age = int(parts[1])
    
    # Notice the efficiency here: we only interact with the exact
    # piece of data we want to change, without touching the name at all.
    if f"age:{sender}" not in storage:
        raise Exception("Profile not found")
        
    storage[f"age:{sender}"] = new_age

else:
    raise Exception("Unknown command. Valid commands: set_grouped, update_age_grouped, set_composite, update_age_composite")
