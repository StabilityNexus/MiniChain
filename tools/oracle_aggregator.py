import asyncio
import aiohttp
import statistics

async def fetch_price(session, exchange_name, url, parse_logic):
    """Fetches and parses price data from an exchange."""
    try:
        async with session.get(url, timeout=5) as response:
            if response.status == 200:
                data = await response.json()
                price = float(parse_logic(data))
                print(f"✅ [{exchange_name}] Price Received: ${price:,.2f}")
                return price
            else:
                print(f"⚠️ [{exchange_name}] returned status {response.status}")
                return None
    except Exception as e:
        print(f"❌ [{exchange_name}] Connection Error: {e}")
        return None

async def main():
    print("--- MiniChain Decentralized Oracle Aggregator ---")
    print("Fetching global ETH/USD consensus...\n")
    
    sources = [
        {"name": "Binance", "url": "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT", "logic": lambda d: d['price']},
        {"name": "CoinGecko", "url": "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd", "logic": lambda d: d['ethereum']['usd']},
        {"name": "Kraken", "url": "https://api.kraken.com/0/public/Ticker?pair=ETHUSD", "logic": lambda d: d['result']['XETHZUSD']['c'][0]}
    ]
    
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_price(session, s['name'], s['url'], s['logic']) for s in sources]
        
        # This is the missing line! We have to WAIT for the results.
        results = await asyncio.gather(*tasks)
        
        # This line defines 'prices' so the error goes away.
        prices = [p for p in results if p is not None]
        
        if len(prices) >= 2:
            final_price = statistics.median(prices)
            
            # --- AI BRAIN TRIGGER ---
            TARGET_PRICE = 2200.00  
            print(f"\n[AI BRAIN] Analyzing current price vs Target (${TARGET_PRICE})...")
            
            if final_price > TARGET_PRICE:
                print("🚀 RESULT: Price is ABOVE target. Suggesting SELL signal.")
            else:
                print("💎 RESULT: Price is BELOW target. Suggesting BUY/HOLD signal.")
            # -------------------------

            variation = max(prices) - min(prices)
            print("\n-------------------------------------------")
            print(f"📊 CONSOLIDATED PRICE: ${final_price:,.2f}")
            print(f"📈 Source Variance:  ${variation:,.2f}")
            print(f"🔗 Data Sources:     {len(prices)}/3 active")
            print("-------------------------------------------")
        else:
            print("\n🚨 ERROR: Critical Oracle failure. Not enough sources for consensus.")

if __name__ == "__main__":
    asyncio.run(main())