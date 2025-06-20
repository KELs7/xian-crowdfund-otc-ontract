balances = Hash(default_value=decimal('0.0'))
metadata = Hash()
TAX_RATE = decimal('0.05') # 5% tax, receiver gets 95%

@construct
def seed():
    initial_supply = decimal('1000000')
    balances[ctx.caller] = initial_supply
    metadata['token_name'] = "TAXABLE POOL TOKEN"
    metadata['token_symbol'] = "TPT"
    metadata['total_supply'] = initial_supply
    metadata['operator'] = ctx.caller

@export
def transfer(amount: float, to: str):
    assert amount > decimal('0.0'), 'Cannot transfer zero or negative!'
    sender = ctx.caller
    
    sender_bal = balances[sender]
    assert sender_bal >= amount, f'Transfer amount exceeds balance for sender {sender}!'
    
    received_amount = amount * (decimal('1.0') - TAX_RATE)
    
    balances[sender] = sender_bal - amount
    balances[to] += received_amount
    
    # Optional: Log transfer event including actual received amount
    # For simplicity, total_supply isn't actively managed here post-tax.

@export
def approve(amount: float, to: str):
    assert amount >= decimal('0.0'), 'Cannot approve negative!' # Allow 0 for clearing approval
    sender = ctx.caller
    balances[sender, to] = amount

@export
def transfer_from(amount: float, to: str, main_account: str):
    assert amount > decimal('0.0'), 'Cannot transfer zero or negative!'
    spender = ctx.caller 
    
    allowance = balances[main_account, spender]
    assert allowance >= amount, \
        f'Transfer amount {amount} exceeds allowance {allowance} for {main_account} by spender {spender}!'
    
    main_account_bal = balances[main_account]
    assert main_account_bal >= amount, f'Transfer amount {amount} exceeds balance {main_account_bal} for main_account {main_account}!'
    
    received_amount = amount * (decimal('1.0') - TAX_RATE)
    
    balances[main_account, spender] = allowance - amount
    balances[main_account] = main_account_bal - amount
    balances[to] += received_amount

@export
def balance_of(address: str):
    return balances[address]

# Helper for testing to check allowance
@export
def allowance(owner: str, spender: str):
    return balances[owner, spender]