<!-- Don't delete it -->
<div name="readme-top"></div>

<!-- Organization Logo -->
<div align="center" style="display: flex; align-items: center; justify-content: center; gap: 16px;">
  <img alt="Stability Nexus" src="public/stability.svg" width="175">
  <!--<img src="public/todo-project-logo.svg" width="175" /> -->
</div>

&nbsp;

<!-- Organization Name -->
<div align="center">

[![Static Badge](https://img.shields.io/badge/Stability_Nexus-MiniChain-228B22?style=for-the-badge&labelColor=FFC517)](https://stability.nexus/)

<!-- Correct deployed url to be added -->

</div>

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
<!-- Medium -->
<a href="https://news.stability.nexus/">
  <img src="https://img.shields.io/badge/Medium-black?style=flat&logo=medium&logoColor=black&logoSize=auto&color=white" alt="Medium Badge"></a>
&nbsp;&nbsp;
<!-- LinkedIn -->
<a href="https://linkedin.com/company/stability-nexus">
  <img src="https://img.shields.io/badge/LinkedIn-black?style=flat&logo=LinkedIn&logoColor=white&logoSize=auto&color=0A66C2" alt="LinkedIn Badge"></a>
&nbsp;&nbsp;
<!-- Youtube -->
<a href="https://www.youtube.com/@StabilityNexus">
  <img src="https://img.shields.io/youtube/channel/subscribers/UCZOG4YhFQdlGaLugr_e5BKw?style=flat&logo=youtube&logoColor=white&logoSize=auto&labelColor=FF0000&color=FF0000" alt="Youtube Badge"></a>
</p>

---

<div align="center">
<h1>MiniChain</h1>
</div>

MiniChain is a minimal fully functional blockchain implemented in Python.

### Background and Motivation

* Most well-known blockchains are now several years old and have accumulated a lot of technical debt.
  Simply forking their codebases is not an optimal option for starting a new chain.

* MiniChain will be focused on research. Its primary purpose is not to be yet another blockchain
  trying to be the one blockchain to kill them all, but rather to serve as a clean codebase that can be a benchmark for research of
  variations of the technology. (We hope that MiniChain will be as valuable for blockchain research as, for instance,
  MiniSat was valuable for satisfiability and automated reasoning research. MiniSat had less than 600 lines of C++ code.)

* MiniChain will be focused on education. By having a clean and small codebase, devs will be able to understand
  blockchains by looking at the codebase.

* The blockchain space is again going through a phase where many new blockchains are being launched.
  Back in 2017 and 2018, such an expansion period led to many general frameworks for blockchains,
  such as Scorex and various Hyperledger frameworks. But most of these frameworks suffered from speculative generality and
  were bloated. They focused on extensibility and configurability. MiniChain has a different philosophy:
  focus on minimality and, therefore, ease of modification.

* Recent advances in networking and crypto libraries for Python make it possible to develop MiniChain in Python.
  Given that Python is one of the easiest languages to learn and results in usually boilerplate-minimized and easy to read code,
  implementing MiniChain in Python aligns with MiniChain's educational goal.


### Overview of Tasks

* Develop a fully functional minimal blockchain in Python, with all the expected components:
  peer-to-peer networking, consensus, mempool, ledger, ...

* Bonus task: add smart contracts to the blockchain. 

Candidates are expected to refine these tasks in their GSoC proposals. 
It is encouraged that you develop an initial prototype during the application phase.

### Requirements

* Use [PyNaCl](https://pynacl.readthedocs.io/en/latest/) library for hashing, signing transactions and verifying signatures.
* Use [Py-libp2p](https://github.com/libp2p/py-libp2p/tree/main) for p2p networking.
* Implement Proof-of-Work as the consensus protocol.
* Use accounts (instead of UTxO) as the accounting model for the ledger.
* Use as few lines of code as possible without compromising readability and understandability.
* For the bonus task, make Python itself be the language used for smart contracts, but watch out for security concerns related to executing arbitrary code from untrusted sources.

### Resources

* Read this book:  https://www.marabu.dev/blockchain-foundations.pdf 


---

## Tech Stack

TODO:

---

## Getting Started

### Prerequisites

TODO

### Installation

TODO

---

## Contributing

We welcome contributions of all kinds!

If you encounter bugs, need help, or have feature requests:

- Please open an issue in this repository providing detailed information.
- Describe the problem clearly and include any relevant logs or screenshots.

We appreciate your feedback and contributions!

© 2025 The Stable Order.
