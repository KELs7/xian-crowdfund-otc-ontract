
balances = Hash(default_value=0)
metadata = Hash()

@construct
def seed():
    balances[ctx.caller] = 1_000_000

    metadata['token_name'] = "TAKE TOKEN"
    metadata['token_symbol'] = "TKT"
    metadata['total_supply'] = balances[ctx.caller]
    metadata['operator'] = ctx.caller

@export
def transfer(amount: float, to: str):
    assert amount > 0, 'Cannot transfer negative!'
    sender = ctx.caller
    assert balances[sender] >= amount, 'Transfer amount exceeds balance!'
    balances[sender] -= amount
    balances[to] += amount

@export
def approve(amount: float, to: str):
    assert amount > 0, 'Cannot approve negative!'
    sender = ctx.caller
    balances[sender, to] = amount

@export
def transfer_from(amount: float, to: str, main_account: str):
    assert amount > 0, 'Cannot transfer negative!'
    sender = ctx.caller
    assert balances[main_account, sender] >= amount, \
        f'Transfer amount exceeds allowance for {main_account}!'
    assert balances[main_account] >= amount, 'Transfer amount exceeds balance!'
    balances[main_account, sender] -= amount
    balances[main_account] -= amount
    balances[to] += amount

@export
def balance_of(address: str):
    return balances[address]
    