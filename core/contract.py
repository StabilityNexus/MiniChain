class ContractMachine:
    """
    A minimal execution environment for Python-based smart contracts.
    """
    def __init__(self, state):
        self.state = state

    def execute(self, contract_address, sender_address, payload, amount):
        """
        Executes the contract code associated with the contract_address.
        
        :param contract_address: Address of the contract to run
        :param sender_address: Address calling the contract
        :param payload: Input data (msg['data'])
        :param amount: Value sent (msg['value'])
        """
        # Retrieve contract
        account = self.state.get_account(contract_address)
        code = account.get('code')
        storage = account.get('storage', {})

        if not code:
            return False

        # Sandbox Context
        # We allow the contract to read/write its own storage and see msg details
        context = {
            'storage': storage,
            'msg': {
                'sender': sender_address,
                'value': amount,
                'data': payload
            },
            'print': print # Allow debug prints
        }

        try:
            # SECURITY WARNING: exec() is unsafe for production blockchains.
            # This is for educational/research purposes only.
            exec(code, {}, context)
            
            # Update storage if execution succeeded
            self.state.update_contract_storage(contract_address, context['storage'])
            return True
        except Exception as e:
            print(f"Contract Execution Failed: {e}")
            return False
