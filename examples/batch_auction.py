# Batch Auction DEX Smart Contract Example
#
# One market (a base asset priced in a quote asset). Orders collect into a batch,
# and `clear` settles every crossing order at a single uniform price. Everyone in
# the batch trades at that same price, so the order in which orders arrived, or
# were put into a block, does not change what anyone gets.
#
# Prices are integers: quote units per one base unit.
#
# Valid Payloads:
# - 'init:<base_asset>:<quote_asset>' (once; e.g. 'init:<creator>.GOLD:' trades GOLD for native coins)
# - 'buy:<amount>:<price>'  (send exactly {quote: amount x price}; buy up to <amount> base at <= <price>)
# - 'sell:<amount>:<price>' (send exactly {base: amount}; sell <amount> base at >= <price>)
# - 'cancel:<order_id>'     (owner only, before the batch is cleared; refunds the escrow)
# - 'clear'                 (anyone; settles the batch and refunds whatever did not trade)
#
# Note: contract code runs with separate globals and locals, so it sticks to plain
# loops instead of functions or comprehensions that would need the top-level names.

MAX_ORDERS = 100  # keeps the gas of every call, including clear, bounded


def mul(a, b):
    # The sandbox rejects `*` (it could build huge strings or lists), so multiply
    # non-negative integers by doubling: O(log b) additions.
    result = 0
    while b > 0:
        if b % 2 == 1:
            result += a
        a += a
        b //= 2
    return result


parts = msg['data'].split(':')
cmd = parts[0]
sender = msg['sender']
paid = {}
for asset in msg['value']:
    if msg['value'][asset] > 0:
        paid[asset] = msg['value'][asset]

if cmd != 'init' and 'base' not in storage:
    raise Exception("Market not initialized")
if cmd in ('init', 'cancel', 'clear') and paid:
    raise Exception("This command takes no value")

if cmd == 'init':
    if 'base' in storage:
        raise Exception("Market already initialized")
    if len(parts) != 3 or parts[1] == parts[2]:
        raise Exception("Usage: init:<base_asset>:<quote_asset>")
    storage['base'] = parts[1]
    storage['quote'] = parts[2]
    storage['next_id'] = 0
    storage['orders'] = {}

elif cmd == 'buy' or cmd == 'sell':
    if len(parts) != 3:
        raise Exception("Usage: buy|sell:<amount>:<price>")
    amount = int(parts[1])
    price = int(parts[2])
    if amount <= 0 or price <= 0:
        raise Exception("Amount and price must be positive")
    if len(storage['orders']) >= MAX_ORDERS:
        raise Exception("Batch is full; call clear first")
    if cmd == 'buy':
        escrow = {storage['quote']: mul(amount, price)}
    else:
        escrow = {storage['base']: amount}
    if paid != escrow:
        raise Exception("Must send exactly the escrow for this order")
    order_id = str(storage['next_id'])
    storage['next_id'] += 1
    storage['orders'][order_id] = [sender, cmd, amount, price]

elif cmd == 'cancel':
    if len(parts) != 2 or parts[1] not in storage['orders']:
        raise Exception("Unknown order")
    owner, side, amount, price = storage['orders'][parts[1]]
    if owner != sender:
        raise Exception("Only the owner can cancel an order")
    del storage['orders'][parts[1]]
    if side == 'buy':
        transfer_out(owner, {storage['quote']: mul(amount, price)})
    else:
        transfer_out(owner, {storage['base']: amount})

elif cmd == 'clear':
    base = storage['base']
    quote = storage['quote']
    orders = storage['orders']

    # (price key, id, amount): buys best (highest) price first, sells best (lowest) first,
    # oldest order first on equal prices.
    buys = []
    sells = []
    total_demand = 0
    for oid in orders:
        owner, side, amount, price = orders[oid]
        if side == 'buy':
            buys.append((-price, int(oid), amount))
            total_demand += amount
        else:
            sells.append((price, int(oid), amount))
    buys = sorted(buys)
    sells = sorted(sells)

    # Try each order price p in ascending order. Demand at p is every buy willing to
    # pay >= p, supply every sell willing to accept <= p. Both are kept as running
    # sums, so the whole search is O(k log k) for k orders.
    candidates = []
    for b in buys:
        candidates.append(-b[0])
    for s in sells:
        candidates.append(s[0])
    candidates = sorted(candidates)

    best_price = 0
    best_volume = 0
    best_gap = 0
    demand = total_demand
    supply = 0
    bi = len(buys) - 1   # walks buys from the lowest price up
    si = 0
    for p in candidates:
        while bi >= 0 and -buys[bi][0] < p:
            demand -= buys[bi][2]
            bi -= 1
        while si < len(sells) and sells[si][0] <= p:
            supply += sells[si][2]
            si += 1
        volume = min(demand, supply)
        gap = abs(demand - supply)
        if volume > best_volume or (volume == best_volume and volume > 0 and gap < best_gap):
            best_price = p
            best_volume = volume
            best_gap = gap

    # Fill by price priority, then age; the marginal order may be partly filled.
    # Buyers pay best_price per unit and get the rest of their escrow back; sellers
    # are paid best_price per unit and get their unsold base back.
    remaining = best_volume
    for b in buys:
        owner, side, amount, price = orders[str(b[1])]
        filled = 0
        if price >= best_price and remaining > 0:
            filled = min(amount, remaining)
            remaining -= filled
        payout = {}
        if filled > 0:
            payout[base] = filled
        refund = mul(amount, price) - mul(filled, best_price)
        if refund > 0:
            payout[quote] = refund
        transfer_out(owner, payout)

    remaining = best_volume
    for s in sells:
        owner, side, amount, price = orders[str(s[1])]
        filled = 0
        if price <= best_price and remaining > 0:
            filled = min(amount, remaining)
            remaining -= filled
        payout = {}
        if filled > 0:
            payout[quote] = mul(filled, best_price)
        if amount - filled > 0:
            payout[base] = amount - filled
        transfer_out(owner, payout)

    storage['orders'] = {}
    storage['last_price'] = best_price
    storage['last_volume'] = best_volume

else:
    raise Exception("Unknown command.")
