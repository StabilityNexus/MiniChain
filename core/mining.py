import logging
import re
from core import Transaction, Block
from consensus import mine_block


logger = logging.getLogger(__name__)

BURN_ADDRESS = "0" * 40


def mine_and_process_block(chain, mempool, pending_nonce_map):
    """
    Mine block and let Blockchain handle validation + state updates.
    DO NOT manually apply transactions again.
    """

    pending_txs = mempool.get_transactions_for_block()

    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=pending_txs,
    )

    mined_block = mine_block(block)

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

            result = chain.state.get_account(tx.receiver) if tx.receiver else None
            if isinstance(result, dict):
                deployed_contracts.append(tx.receiver)

        return mined_block, deployed_contracts
    else:
        logger.error("Block rejected by chain")
        return None, []


def sync_nonce(state, pending_nonce_map, address):
    account = state.get_account(address)
    if account and "nonce" in account:
        pending_nonce_map[address] = account["nonce"]
    else:
        pending_nonce_map[address] = 0
