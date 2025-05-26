random.seed()
I = importlib

pool_fund = Hash()
otc_deal_info = Hash() # To store details about the OTC interaction for each pool
contributor = Hash()
metadata = Hash()

# Standard XSC001 (Fungible Token) interface
token_interface = [
    I.Func('transfer_from', args=('amount', 'to', 'main_account')),
    I.Func('transfer', args=('amount', 'to')),
    importlib.Func('balance_of', args=('address',)),
]

@construct
def seed():
    metadata['operator'] = ctx.caller
    metadata['otc_contract'] = 'con_otc' # IMPORTANT: Set to your actual deployed OTC contract name
    metadata['description_length'] = 200
    metadata['contribution_window'] = 5 * datetime.DAYS # Using the time unit
    metadata['exchange_window'] = 3 * datetime.DAYS   # Using the time unit

@export
def change_metadata(key: str, value: Any):
    assert ctx.caller == metadata['operator'], 'Only operator can set metadata!'
    metadata[key] = value

@export
def create_pool(description: str, pool_token: str, hard_cap: float, soft_cap: float):
    assert len(description) <= metadata['description_length'], f"description too long should be <{metadata['description_length']}"
    assert hard_cap > soft_cap, 'hard cap amount should be greater than soft cap amount'
    assert soft_cap > decimal(0), 'soft cap must be positive'

    token_contract = I.import_module(pool_token)
    assert I.enforce_interface(token_contract, token_interface), 'pool_token contract not XSC001-compliant'

    # Create a unique pool ID
    pool_id = hashlib.sha256(str(now) + str(random.randrange(99)))
    assert not pool_fund[pool_id], 'Generated ID not unique. Try again with slight variation or wait a moment.'

    pool_fund[pool_id] = {
        "description": description,
        "pool_token": pool_token,
        "contribution_deadline": now + metadata['contribution_window'],
        "exchange_deadline": now + metadata['contribution_window'] + metadata['exchange_window'],
        "hard_cap": decimal(hard_cap), # Ensure decimals
        "soft_cap": decimal(soft_cap), # Ensure decimals
        "amount_received": decimal(0.0),
        "pool_creator": ctx.caller,
        "status": "OPEN_FOR_CONTRIBUTION", # "OPEN_FOR_CONTRIBUTION", "PENDING_OTC", "OTC_LISTED", "OTC_EXECUTED", "OTC_FAILED", "REFUNDING"
        "otc_listing_id": None,
        "otc_take_token": None,
        "otc_actual_received_amount": decimal(0.0) # Amount of take_token actually received
    }
    return pool_id

@export
def contribute(pool_id: str, amount: float):
    pool = pool_fund[pool_id]
    assert pool, 'pool does not exist'
    assert pool["status"] == "OPEN_FOR_CONTRIBUTION", 'pool not accepting contributions or in wrong state.'
    assert now < pool["contribution_deadline"], 'contribution window closed.'

    dec_amount = decimal(amount)
    assert dec_amount > decimal(0.0), 'contribution amount must be positive.'
    assert pool["amount_received"] + dec_amount <= pool["hard_cap"], 'contribution exceeds hard cap.'

    # Transfer token from contributor to this contract (con_otc_crowdfund)
    I.import_module(pool["pool_token"]).transfer_from(
        amount=dec_amount,
        to=ctx.this,
        main_account=ctx.caller
    )

    pool["amount_received"] += dec_amount
    
    funder_info = contributor[ctx.caller, pool_id]
    if funder_info:
        funder_info["amount_contributed"] += dec_amount
    else:
        funder_info = {"amount_contributed": dec_amount, "share_withdrawn": False}
    
    contributor[ctx.caller, pool_id] = funder_info
    pool_fund[pool_id] = pool

@export
def list_pooled_funds_on_otc(pool_id: str, otc_take_token: str, otc_total_take_amount: float):
    pool = pool_fund[pool_id]
    assert pool, 'pool does not exist'
    assert ctx.caller == pool["pool_creator"], 'Only pool creator can initiate OTC listing.'
    assert pool["status"] == "OPEN_FOR_CONTRIBUTION" or pool["status"] == "PENDING_OTC", "Pool not in correct state to list on OTC."
    assert now > pool["contribution_deadline"], 'Cannot list on OTC before contribution deadline.'
    assert now < pool["exchange_deadline"], 'Exchange window has passed for OTC listing.'
    assert pool["amount_received"] >= pool["soft_cap"], 'Soft cap not met, cannot proceed to OTC.'
    assert pool["otc_listing_id"] is None, 'OTC deal already initiated for this pool.'

    dec_total_take_amount = decimal(otc_total_take_amount)
    assert dec_total_take_amount > decimal(0.0), "OTC take amount must be positive."

    # Verify the take_token contract
    take_token_contract = I.import_module(otc_take_token)
    assert I.enforce_interface(take_token_contract, token_interface), 'otc_take_token contract not XSC001-compliant'

    otc_contract = I.import_module(metadata['otc_contract'])
    
    # The crowdfund contract (ctx.this) lists its pooled tokens on the OTC exchange
    # `list_offer` expects `transfer_from` to be callable on `offer_token` from `ctx.caller` (which is `ctx.this` here)
    # Since the tokens are already in `ctx.this`, this is effectively `ctx.this` allowing `otc_contract` to take them.
    # The OTC contract's `list_offer` will internally do a transfer_from itself, with main_account=ctx.this (crowdfund).
    # This is fine as the crowdfund contract is the one calling `list_offer`.

    listing_id = otc_contract.list_offer(
        offer_token=pool["pool_token"],
        offer_amount=pool["amount_received"], # Offer all pooled funds
        take_token=otc_take_token,
        take_amount=dec_total_take_amount
    )

    assert listing_id, "Failed to get a listing ID from OTC contract."

    pool["otc_listing_id"] = listing_id
    pool["otc_take_token"] = otc_take_token
    # pool["otc_expected_take_amount"] = dec_total_take_amount # Stored for reference, actual might differ if partial fills were allowed by OTC
    pool["status"] = "OTC_LISTED"
    pool_fund[pool_id] = pool
    
    otc_deal_info[pool_id] = { # Store basic info about the attempt
        "listing_id": listing_id,
        "target_take_token": otc_take_token,
        "target_take_amount": dec_total_take_amount,
        "listed_pool_token_amount": pool["amount_received"]
    }
    return listing_id

@export
def withdraw_contribution(pool_id: str):
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]

    assert pool, 'pool does not exist'
    assert funder and funder["amount_contributed"] > decimal(0.0), 'no contribution to withdraw or already withdrawn.'

    can_withdraw = False
    # Reason 1: Before contribution deadline (and OTC not yet attempted)
    if pool["status"] == "OPEN_FOR_CONTRIBUTION" and now < pool["contribution_deadline"]:
        can_withdraw = True
    
    # Reason 2: OTC deal failed or expired, and pool is in refunding state
    # This state would be set by a manager after checking OTC, or implicitly if exchange_deadline passed
    if pool["status"] == "REFUNDING": # This status needs to be set by a separate management function or check
         can_withdraw = True
    
    # Reason 3: Implicit refund if exchange window passed and deal not completed
    if not pool["exchange_completed"] and now > pool["exchange_deadline"]: # 'exchange_completed' is a simplified flag here
        # More robust: check OTC listing status if listed
        if pool["otc_listing_id"]:
            otc_listing_details = I.import_module(metadata['otc_contract']).otc_listing[pool["otc_listing_id"]]
            if otc_listing_details and (otc_listing_details["status"] == "CANCELLED" or (otc_listing_details["status"] == "OPEN" and now > pool["exchange_deadline"])):
                can_withdraw = True
                if pool["status"] != "REFUNDING": # Mark for refund if not already
                    pool["status"] = "REFUNDING" # Or "OTC_FAILED"
                    pool_fund[pool_id] = pool
        else: # Not even listed on OTC, and window passed
            can_withdraw = True
            if pool["status"] != "REFUNDING":
                 pool["status"] = "REFUNDING" # Or "CONTRIBUTIONS_CLOSED_NO_OTC"
                 pool_fund[pool_id] = pool


    assert can_withdraw, 'Withdrawal not allowed at this stage.'

    amount_to_withdraw = funder["amount_contributed"]
    I.import_module(pool["pool_token"]).transfer(
        amount=amount_to_withdraw,
        to=ctx.caller
    )
    pool["amount_received"] -= amount_to_withdraw
    pool_fund[pool_id] = pool

    funder["amount_contributed"] = decimal(0.0) # Mark as withdrawn
    contributor[ctx.caller, pool_id] = funder

@export
def finalize_otc_deal_status(pool_id: str):
    """
    A function callable by anyone (or pool_creator) to update the pool's status
    based on the OTC exchange. This helps manage state transitions.
    """
    pool = pool_fund[pool_id]
    assert pool, "Pool does not exist."
    assert pool["otc_listing_id"], "Pool was not listed on OTC."
    # Prevent unnecessary calls if already finalized
    assert pool["status"] == "OTC_LISTED", "Pool not in OTC_LISTED state."
    assert now > pool["contribution_deadline"], "Too early to finalize." # Can be called during exchange window

    otc_contract = I.import_module(metadata['otc_contract'])
    otc_offer_details = otc_contract.otc_listing[pool["otc_listing_id"]] # Assumes otc_listing is a public hash

    assert otc_offer_details, "OTC listing details not found."

    if otc_offer_details["status"] == "EXECUTED":
        pool["status"] = "OTC_EXECUTED"
        # Record the actual amount of take_token this contract should have received.
        # The OTC contract transfers `otc_offer_details["take_amount"]` to the maker (`ctx.this`).
        pool["otc_actual_received_amount"] = otc_offer_details["take_amount"]
        pool_fund[pool_id] = pool
        
        # Update otc_deal_info as well
        deal_info = otc_deal_info[pool_id]
        if deal_info:
            deal_info["status"] = "EXECUTED"
            deal_info["actual_received_amount"] = otc_offer_details["take_amount"]
            otc_deal_info[pool_id] = deal_info

    elif otc_offer_details["status"] == "CANCELLED" or (otc_offer_details["status"] == "OPEN" and now > pool["exchange_deadline"]):
        pool["status"] = "OTC_FAILED" # Or "REFUNDING"
        pool_fund[pool_id] = pool
        deal_info = otc_deal_info[pool_id]
        if deal_info:
            deal_info["status"] = "FAILED_OR_EXPIRED"
            otc_deal_info[pool_id] = deal_info
    # If still "OPEN" and within exchange_deadline, do nothing, let it play out.
    else:
        # Still open and within window
        return "OTC deal still open."
        
    return f"Pool status updated to {pool['status']}"


@export
def withdraw_share(pool_id: str):
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]

    assert pool, 'pool does not exist'
    assert pool["status"] == "OTC_EXECUTED", 'OTC deal not successfully executed yet. Try calling finalize_otc_deal_status.'
    assert funder and funder["amount_contributed"] > decimal(0.0), 'no original contribution to claim a share for.'
    assert not funder["share_withdrawn"], 'share already withdrawn.'
    assert pool["amount_received"] > decimal(0.0), 'Initial pool amount is zero, cannot calculate share.' # Should not happen if softcap met

    # Calculate share based on actual amount received from otc
    share_percentage = funder["amount_contributed"] / pool["otc_actual_received_amount"]
    
    # The amount to withdraw is this share percentage of the otc_actual_received_amount
    amount_of_take_token_to_withdraw = share_percentage * pool["otc_actual_received_amount"]

    assert amount_of_take_token_to_withdraw > decimal(0.0), "Calculated share is zero."

    I.import_module(pool["otc_take_token"]).transfer(
        amount=amount_of_take_token_to_withdraw,
        to=ctx.caller
    )

    funder["share_withdrawn"] = True
    contributor[ctx.caller, pool_id] = funder

# --- Helper/View functions (optional) ---
@export
def get_pool_info(pool_id: str):
    return pool_fund[pool_id]

@export
def get_contribution_info(pool_id: str, account: str):
    return contributor[account, pool_id]

@export
def get_otc_deal_info_for_pool(pool_id: str):
    return otc_deal_info[pool_id]