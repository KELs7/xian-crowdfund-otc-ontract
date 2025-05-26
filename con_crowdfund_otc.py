random.seed()
I = importlib

pool_fund = Hash()
otc_deal = Hash()
contributor = Hash()
metadata = Hash()

token_interface = [
    importlib.Func('transfer_from', args=('amount', 'to', 'main_account')),
    importlib.Func('transfer', args=('amount', 'to')),
    importlib.Func('balance_of', args=('address',)),
]

@construct
def seed():
    metadata['operator'] = ctx.caller
    metadata['otc_contract'] = 'con_otc_crowdfund'
    metadata['description_length'] = 50
    metadata['contribution_window'] = 5days
    metadata['exchange_window'] = 3days

@export
def change_metadata(key: str, value: Any):
    assert ctx.caller == metadata['operator'], 'Only operator can set metadata!'
    metadata[key] = value

@export
def create_pool(description: str, pool_token: str, hard_cap: float, soft_cap: float):
    assert len(description) <= metadata['description_length'], f"description too long should be <{metadata['description_length']}"
    assert hard_cap > soft_cap, 'hard cap amount should be greater than soft cap amount'
    token_contract = I.import_module(pool_token)
    assert importlib.enforce_interface(token_contract, token_interface), 'token contract not XSC001-compliant'
    pool_id = hashlib.sha256(str(now) + str(random.randrange(99)))
    assert not pool_fund[pool_id], 'Generated ID not unique. Try again'

    pool_fund[pool_id] = {
        "description": description,
        "pool_token": pool_token,
        "contribution_deadline": now + metadata['contribution_window'],
        "exchange_deadline": now + metadata['contribution_window'] + metadata['exchange_window'],
        "hard_cap": hard_cap,
        "soft_cap": soft_cap,
        "amount_received": decimal(0.0),
        "pool_creator": ctx.caller,
        "exchange_completed": False
    }

@export
def contribute(pool_id: str, amount: float):
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]
    assert pool, 'pool does not exist'
    assert now < pool["contribution_deadline"], 'pool not accepting anymore contributions'
    
    I.import_module(pool["pool_token"]).transfer_from(
        amount=amount,
        to=ctx.this,
        main_account=ctx.caller
    )

    pool["amount_received"] = pool["amount_received"] + amount
    pool_fund[pool_id] = pool

    if funder:
        funder["amount_contributed"] = funder["amount_contributed"] + amount
        contributor[ctx.caller, pool_id] = funder
    else:
        contributor[ctx.caller, pool_id] = {
            "amount_contributed": amount
        }   

@export
def withdraw_contribution(pool_id: str):
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]
    assert pool, 'pool does not exist'
    assert now < pool["contribution_deadline"] or (now > pool["exchange_deadline"] and not pool["exchange_completed"]), 'can only withdraw funds before contribution deadline and after exchange deadline'
    assert funder and funder["amount_contributed"] > decimal(0.0), 'cannot withdraw if there is no contribution amount'

    amount_contributed = funder["amount_contributed"]
    I.import_module(pool["pool_token"]).transfer(
        amount=amount_contributed,
        to=ctx.caller
    )
    pool["amount_received"] = pool["amount_received"] - amount_contributed
    pool_fund[pool_id] = pool

    funder["amount_contributed"] = decimal(0.0)
    contributor[ctx.caller, pool_id] = funder

@export
def withdraw_share(pool_id: str):
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]
    otc = otc_deal[pool_id]
    
    assert pool["exchange_completed"] and now > pool["contribution_deadline"], 'cannot withdraw share'

    share = (funder["amount_contributed"]/pool["amount_received"]) * otc["offer_amount"]

    I.import_module(otc["offer_token"]).transfer(
        amount=share,
        to=ctx.caller
    )

@export
def withdraw_to_otc(pool_id: str, offer_token: str, offer_amount: float):
    pool = pool_fund[pool_id]
    assert ctx.caller == metadata['otc_contract'], 'Only otc contract can call this function!'
    assert pool, 'pool does not exist'
    assert not pool['exchange_completed'], 'pool has been involved in an otc deal'
    assert  pool["amount_reeived"] > pool["soft_cap"], 'cannot do an otc deal because funds is below soft cap'
    assert now > pool["contribution_deadline"] and now < pool["exchange_deadline"], 'can only do otc deal within exchange window'

    I.import_module(pool["pool_token"]).transfer(
        amount=pool["amount_received"],
        to=metadata['otc_contract']
    )