import logging
import re
from minichain import Transaction, Block
from minichain.pow import mine_block, MiningExceededError


logger = logging.getLogger(__name__)

BURN_ADDRESS = "0" * 40


def mine_and_process_block(chain, mempool, pending_nonce_map):
    pending_txs = mempool.get_transactions_for_block()
    tx_hashes = [mempool._get_tx_id(tx) for tx in pending_txs]

    last_block = chain.last_block
    block = Block(
        index=last_block.index + 1,
        previous_hash=last_block.hash,
        transactions=pending_txs,
    )

    try:
        mined_block = mine_block(block)
    except MiningExceededError:
        mempool.return_transactions(pending_txs)
        logger.warning("Mining failed, transactions returned to mempool")
        return None, []

    if not hasattr(mined_block, "miner"):
        mined_block.miner = BURN_ADDRESS

    deployed_contracts: list[str] = []

    if chain.add_block(mined_block):
        logger.info("Block #%s added", mined_block.index)

        miner_attr = getattr(mined_block, "miner", None)
        if isinstance(miner_attr, str) and re.match(r'^[0-9a-fA-F]{40}$', miner_attr):
            miner_address = miner_attr
        else:
            logger.warning("Invalid miner address. Crediting burn address.")
            miner_address = BURN_ADDRESS

        chain.state.credit_mining_reward(miner_address)

        for tx in mined_block.transactions:
            sync_nonce(chain.state, pending_nonce_map, tx.sender)
            if tx.receiver is None:
                contract_address = chain.state.derive_contract_address(tx.sender, tx.nonce)
                deployed_contracts.append(contract_address)

        return mined_block, deployed_contracts
    else:
        mempool.return_transactions(pending_txs)
        logger.error("Block rejected by chain, transactions returned to mempool")
        return None, []


def sync_nonce(state, pending_nonce_map, address):
    account = state.get_account(address)
    if account and "nonce" in account:
        pending_nonce_map[address] = account["nonce"]
    else:
        pending_nonce_map[address] = 0
