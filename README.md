<!-- Don't delete it -->
<div name="readme-top"></div>

<!-- Organization Logo -->
<div align="center" style="display: flex; align-items: center; justify-content: center; gap: 16px;">
  <img alt="Stability Nexus" src="public/stability.svg" width="175">
  <img alt="MiniChain" src="public/minichain.svg" width="175" />
</div>

&nbsp;

<!-- Organization Name -->
<div align="center">

[![Static Badge](https://img.shields.io/badge/Stability_Nexus-MiniChain-228B22?style=for-the-badge&labelColor=FFC517)](https://stability.nexus/)

<!-- Organization/Project Social Handles -->
<p align="center">
<!-- Telegram -->
<a href="https://t.me/StabilityNexus">
<img src="https://img.shields.io/badge/Telegram-black?style=flat&logo=telegram&logoColor=white&logoSize=auto&color=24A1DE" alt="Telegram Badge"/></a>
&nbsp;&nbsp;
<!-- X (formerly Twitter) -->
<a href="https://x.com/StabilityNexus">
<img src="https://img.shields.io/twitter/follow/StabilityNexus" alt="X (formerly Twitter) Badge"/></a>
&nbsp;&nbsp;
<!-- Discord -->
<a href="https://discord.gg/YzDKeEfWtS">
<img src="https://img.shields.io/discord/995968619034984528?style=flat&logo=discord&logoColor=white&logoSize=auto&label=Discord&labelColor=5865F2&color=57F287" alt="Discord Badge"/></a>
&nbsp;&nbsp;
<!-- Blogs -->
<a href="https://viewpoints.stability.nexus/">
  <img src="https://img.shields.io/badge/Stable_Viewpoints-Articles-2ea44f?style=flat&labelColor=facc15" alt="Stable Viewpoints"></a>
&nbsp;&nbsp;
<!-- LinkedIn -->
<a href="https://linkedin.com/company/stability-nexus">
  <img src="https://img.shields.io/badge/LinkedIn-black?style=flat&logo=LinkedIn&logoColor=white&logoSize=auto&color=0A66C2" alt="LinkedIn Badge"></a>
&nbsp;&nbsp;
<!-- Youtube -->
<a href="https://www.youtube.com/@StabilityNexus">
  <img src="https://img.shields.io/youtube/channel/subscribers/UCZOG4YhFQdlGaLugr_e5BKw?style=flat&logo=youtube&logoColor=white&logoSize=auto&labelColor=FF0000&color=FF0000" alt="Youtube Badge"></a>
</p>

<!-- Pytest Coverage Comment:Begin -->

<!-- Pytest Coverage Comment:End -->

</div>


---

<div align="center">
<h1>MiniChain</h1>
</div>

MiniChain is a minimal fully functional blockchain implemented in Python, with 3 goals:

* **Education:** By having a clean codebase with self-explanatory code, MiniChain allows devs to learn and deeply understand blockchains.
MiniChain is implemented in Python, which is one of the most popular and easy-to-learn programming languages. MiniChain's smart contracts can be written in Python as well.
Furthermore, recent advances in Python's networking and cryptography libraries allow MiniChain to fulfill its educational goal while maintaining performance and security.

* **Research:** By having a small codebase, MiniChain is easy to modify by researchers who are interested in exploring variations of blockchain technologies (e.g. consensus protocols, smart contract approaches, scalibility solutions, ...) and in having a benchamark against which these variations can be tested. We hope that MiniChain will be as valuable for blockchain research as, for instance, MiniSat (with its less than 600 lines of C++ code) was valuable for satisfiability and automated reasoning research.

* **Innovation:** MiniChain is a minimal technical-debt-free codebase that can be forked to create new custom blockchains.
The blockchain space is again going through a phase where many new blockchains are being launched.
Similar expansion periods in the past led either to forks of codebases that carried technical debt
or to development and use of various general blockchain frameworks that suffered from speculative generality.
They focused on extensibility and configurability. MiniChain has a different philosophy:
it focuses on minimality and, therefore, ease of modification.

---

## Getting Started

### Prerequisites

- Python 3.10+
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

### 1. Creating a New MiniChain
To bootstrap a brand new blockchain network from scratch, simply start a node. By default, this creates a new Genesis block.
```bash
python main.py --port 9000 --datadir ./node1_data
```
*Note: Keep this terminal open to interact with the node via the CLI.*

### 2. Connecting to an Existing Chain
To connect a secondary node to the network, start a new instance on a different port and point it to the seed node using the `--connect` flag with the full multiaddress the seed node printed on startup (it includes the seed's peer ID, not just its host and port).
```bash
python main.py --port 9001 --connect /ip4/127.0.0.1/tcp/9000/p2p/<seed-peer-id> --datadir ./node2_data
```
The node will automatically sync the blockchain state via the P2P network using the Fork-Choice rule.

### 3. Mining Blocks
To confirm pending transactions, you need to mine blocks. In the interactive CLI of your node, simply type:
```text
minichain> mine
```
This runs the Proof-of-Work algorithm, validates transactions, computes the new state root, updates your wallet with the block reward + fees, and broadcasts the block to all connected peers.

---

## Basic Operations (Interactive CLI)

Once your node is running, you can perform basic blockchain operations directly in your terminal.

**Making a Transfer**
Send coins to another public key:
```text
minichain> send <receiver_address> <amount> <fee>
```
*Example: `send 8b3401abedb875aff7279b5ab58cb9a0c... 100 1`*

**Checking Balances**
View the state of all active accounts and contracts on the chain:
```text
minichain> balance
```

**Viewing Network State**
```text
minichain> chain   # View all blocks
minichain> peers   # View connected P2P nodes
minichain> address # View your own public key
```

---

## Smart Contracts

MiniChain supports fully-functional smart contracts written directly in Python! 
The execution engine uses `sys.settrace` for precise **Gas Metering** (charging 1 gas per executed opcode) and `multiprocessing` for **Sandboxed Execution** to ensure network security.

### Writing a Contract
Smart contracts in MiniChain have access to a persistent `storage` dictionary and a `msg` dictionary containing transaction context (`sender`, `value`, `data`).

Check out the `/examples` directory for tutorials:
- `examples/counter.py` - A basic state mutation example.
- `examples/stablecoin.py` - A minimal ERC-20 style fungible token.
- `examples/dex.py` - An Automated Market Maker (AMM) using the constant product formula (x * y = k).

### Interacting via CLI
Start the interactive node using `python main.py` and use the following commands:
1. **Deploy:** `deploy <filepath> [amount] [fee]`
2. **Call:** `call <contract_address> <payload> [amount] [fee]`

Example deployment:
```text
minichain> deploy examples/counter.py 0 100
```

---

## JSON-RPC 2.0 Server

MiniChain automatically spins up a JSON-RPC 2.0 server alongside the P2P node. By default, it binds to `port 8545` (the standard EVM RPC port). External wallets and dApps can use this to interact with the chain asynchronously.

**Example Request (Get Block Number):**
```bash
curl -X POST http://127.0.0.1:8545/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "mc_blockNumber", "id": 1}'
```

Available endpoints include: `mc_blockNumber`, `mc_getBlockByNumber`, `mc_getBalance`, and `mc_sendTransaction`.

---

## Contributing

We welcome contributions of all kinds!

If you encounter bugs, need help, or have feature requests:

- Please open an issue in this repository providing detailed information.
- Describe the problem clearly and include any relevant logs or screenshots.

We appreciate your feedback and contributions!


### Resources

* If you would like to learn blockchain technologies, we recommend reading this book (https://www.marabu.dev/blockchain-foundations.pdf) in addition to inspecting MiniChain's codebase.

---

© 2025 The Stable Order.
