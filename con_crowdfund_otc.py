random.seed()
I = importlib

pool_fund = Hash()
otc_deal_info = Hash() # To store details about the OTC interaction for each pool
contributor = Hash()
metadata = Hash()

# New state variable for re-entrancy guard
reentrancyGuardActive = Variable(default_value=False)

# Standard XSC001 (Fungible Token) interface
token_interface = [
    I.Func('transfer_from', args=('amount', 'to', 'main_account')),
    I.Func('transfer', args=('amount', 'to')),
    importlib.Func('balance_of', args=('address',)),
]

# Events
PoolCreated = LogEvent(
    event="pool_created", 
    params={
        "id":{'type':str, 'idx':True}, 
        "description": {'type':str, 'idx':False},
        "pool_token": {'type':str, 'idx':False}, 
        "hard_cap": {'type':(int, float, decimal)},
        "soft_cap": {'type':(int, float, decimal)},
        "contribution_deadline": {'type':str, 'idx':False},
        "exchange_deadline": {'type':str, 'idx':False}
    })

PoolListedOTC = LogEvent(
    event="pool_listed_on_otc", 
    params={
        "otc_listing_id":{'type':str, 'idx':True}, 
        "pool_id": {'type':str, 'idx':False},
        "pool_token": {'type':str, 'idx':False}, 
        "pool_token_amount": {'type':(int, float, decimal)},
        "otc_take_token": {'type':str, 'idx':False},
        "otc_total_take_amount": {'type':(int, float, decimal)}
    })

CancelledListing = LogEvent(
    event="listing_cancelled", 
    params={
        "otc_listing_id":{'type':str, 'idx':True}, 
        "pool_id": {'type':str, 'idx':False},
    })

Contribution = LogEvent(
    event="contribution", 
    params={ 
        "pool_id": {'type':str, 'idx':True},
        "amount": {'type':(int, float, decimal)},
        "pool_amount": {'type':(int, float, decimal)}
    })

@construct
def seed():
    metadata['operator'] = ctx.caller
    metadata['otc_contract'] = 'con_otc' # IMPORTANT: Set to your actual deployed OTC contract name
    metadata['description_length'] = 200
    metadata['contribution_window'] = datetime.DAYS * 5 
    metadata['exchange_window'] = datetime.DAYS * 3
    reentrancyGuardActive.set(False) # Initialize re-entrancy guard

@export
def change_metadata(key: str, value: Any):
    # This function doesn't make external calls, but respecting the guard is good practice
    # if it's meant to be a global lock during any state-modifying operation.
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, cannot change metadata now."
    assert ctx.caller == metadata['operator'], 'Only operator can set metadata!'
    metadata[key] = value

@export
def create_pool(description: str, pool_token: str, hard_cap: float, soft_cap: float):
    # This function doesn't make external calls that could lead to re-entrancy into itself
    # before its own state changes are complete. No guard needed here unless for global lock policy.
    assert len(description) <= metadata['description_length'], f"description too long should be <{metadata['description_length']}"
    assert hard_cap > soft_cap, 'hard cap amount should be greater than soft cap amount'
    assert soft_cap > decimal("0.0"), 'soft cap must be positive'

    token_contract = I.import_module(pool_token)
    assert I.enforce_interface(token_contract, token_interface), 'pool_token contract not XSC001-compliant'

    pool_id = hashlib.sha256(str(now) + str(random.randrange(99)))
    assert not pool_fund[pool_id], 'Generated ID not unique. Try again with slight variation or wait a moment.'

    pool_fund[pool_id] = {
        "description": description,
        "pool_token": pool_token,
        "contribution_deadline": now + metadata['contribution_window'],
        "exchange_deadline": now + metadata['contribution_window'] + metadata['exchange_window'],
        "hard_cap": hard_cap,
        "soft_cap": soft_cap,
        "amount_received": decimal("0.0"),
        "pool_creator": ctx.caller,
        "status": "OPEN_FOR_CONTRIBUTION",
        "otc_listing_id": None,
        "otc_take_token": None,
        "otc_actual_received_amount": decimal("0.0")
    }

    pool = pool_fund[pool_id]

    PoolCreated({
        "id": pool_id, 
        "description": pool["description"],
        "pool_token": pool["pool_token"], 
        "hard_cap": pool["hard_cap"],
        "soft_cap": pool["soft_cap"],
        "contribution_deadline": str(pool["contribution_deadline"]),
        "exchange_deadline": str(pool["exchange_deadline"])
    })
    return pool_id

@export
def contribute(pool_id: str, amount: float):
    # --- RE-ENTRANCY GUARD & CHECKS-EFFECTS-INTERACTIONS PATTERN ---
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, please try again."
    reentrancyGuardActive.set(True)

    # --- CHECKS ---
    pool = pool_fund[pool_id]
    assert pool, 'pool does not exist'
    assert now < pool["contribution_deadline"], 'contribution window closed.'
    assert amount > decimal("0.0"), 'contribution amount must be positive.'
    assert pool["amount_received"] + amount <= pool["hard_cap"], 'contribution exceeds hard cap.'

    # --- EFFECTS (BEFORE INTERACTION) ---
    # 1. Update pool's total amount_received
    pool["amount_received"] += amount
    pool_fund[pool_id] = pool

    # 2. Update contributor's specific information
    funder = contributor[ctx.caller, pool_id]
    
    if funder:
        funder["amount_contributed"] += amount
    else:
        funder = {"amount_contributed": amount, "share_withdrawn": False}

    contributor[ctx.caller, pool_id] = funder

    # --- INTERACTION ---
    # If this transfer_from fails, the transaction aborts, rolling back all state changes.
    pool_token_contract_address = pool["pool_token"] 
    token_contract_module = I.import_module(pool_token_contract_address)
    token_contract_module.transfer_from(
        amount=amount,
        to=ctx.this, 
        main_account=ctx.caller
    )

    Contribution({
        "pool_id": pool_id, 
        "amount": amount, 
        "pool_amount": pool["amount_received"]
    })

    reentrancyGuardActive.set(False) # Deactivate Guard

@export
def list_pooled_funds_on_otc(pool_id: str, otc_take_token: str, otc_total_take_amount: float):
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, please try again."
    reentrancyGuardActive.set(True)

    pool = pool_fund[pool_id]
    assert pool, 'pool does not exist'
    assert ctx.caller == pool["pool_creator"], 'Only pool creator can initiate OTC listing.'
    assert now > pool["contribution_deadline"], 'Cannot list on OTC before contribution deadline.'
    assert now < pool["exchange_deadline"], 'Exchange window has passed for OTC listing.'
    assert pool["amount_received"] >= pool["soft_cap"], 'Soft cap not met, cannot proceed to OTC.'
    assert pool["otc_listing_id"] is None, 'OTC deal already initiated for this pool.'
    assert otc_total_take_amount > decimal("0.0"), "OTC take amount must be positive."

    take_token_contract = I.import_module(otc_take_token)
    assert I.enforce_interface(take_token_contract, token_interface), 'otc_take_token contract not XSC001-compliant'

    otc_contract = I.import_module(metadata['otc_contract'])
    pool_token_contract = I.import_module(pool["pool_token"])
    
    # Interaction 1: Approve OTC contract to spend pool_tokens
    # This is an external call. Standard approve usually doesn't re-enter.
    pool_token_contract.approve(amount=pool['amount_received'], to=metadata['otc_contract'])
    
    otc_fee_foreign = ForeignVariable(foreign_contract=metadata['otc_contract'], foreign_name='fee')
    current_otc_fee_percent = otc_fee_foreign.get()
    
    denominator = decimal('1.0') + current_otc_fee_percent / decimal('100.0')
    assert denominator != decimal('0.0'), "Cannot calculate offer amount with current fee yielding a zero divisor."
    net_offer_amount_for_otc = pool["amount_received"] / denominator
    assert net_offer_amount_for_otc > decimal("0.0"), "Calculated net offer amount for OTC is not positive."

    # Interaction 2: List offer on OTC contract
    # OTC contract has its own re-entrancy guard.
    listing_id = otc_contract.list_offer(
        offer_token=pool["pool_token"],
        offer_amount=net_offer_amount_for_otc,
        take_token=otc_take_token,
        take_amount=otc_total_take_amount
    )
    assert listing_id, "Failed to get a listing ID from OTC contract."

    # Effects (after interactions, relies on guards in this contract and OTC contract)
    pool["otc_listing_id"] = listing_id
    pool["otc_take_token"] = otc_take_token
    pool["status"] = "OTC_LISTED"
    pool_fund[pool_id] = pool
    
    otc_deal_info[pool_id] = {
        "listing_id": listing_id,
        "target_take_token": otc_take_token,
        "target_take_amount": otc_total_take_amount,
        "listed_pool_token_amount": pool["amount_received"] # Original amount received
    }

    PoolListedOTC({
        "otc_listing_id":listing_id, 
        "pool_id": pool_id,
        "pool_token": pool["pool_token"], # or pool["pool_token"]
        "pool_token_amount": pool["amount_received"], # Log the total amount put up for OTC
        "otc_take_token": otc_take_token,
        "otc_total_take_amount": otc_total_take_amount
    })
    
    reentrancyGuardActive.set(False)
    return listing_id

@export
def cancel_otc_listing_for_pool(pool_id: str):
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, please try again."
    reentrancyGuardActive.set(True)

    pool = pool_fund[pool_id]
    assert pool, "Pool does not exist."
    assert ctx.caller == pool['pool_creator'] or ctx.caller == metadata['operator'], \
        "Only pool creator or operator can cancel the OTC listing."
    assert pool['otc_listing_id'], "No OTC listing ID found for this pool to cancel."
    assert pool['status'] == "OTC_LISTED" or \
           (pool['status'] == "OTC_FAILED" and now > pool['exchange_deadline']), \
           "Pool not in a state suitable for OTC cancellation via this function, or OTC listing might not be active."

    otc_listings_foreign = ForeignHash(foreign_contract=metadata['otc_contract'], foreign_name='otc_listing')
    otc_offer_details = otc_listings_foreign[pool["otc_listing_id"]]
    assert otc_offer_details, "OTC listing details not found on the exchange contract."
    assert otc_offer_details["status"] == "OPEN", \
        f"OTC offer is not OPEN (current status: {otc_offer_details['status']}). Cannot cancel via crowdfund if already finalized on OTC."

    # Interaction: Call cancel_offer on OTC contract. OTC contract has its own guard.
    otc_contract = I.import_module(metadata['otc_contract'])
    otc_contract.cancel_offer(listing_id=pool['otc_listing_id'])

    # Effects (after interaction)
    pool['status'] = "OTC_FAILED"
    pool_fund[pool_id] = pool

    deal_info = otc_deal_info[pool_id]
    if deal_info:
        deal_info["status"] = "CANCELLED"
        otc_deal_info[pool_id] = deal_info

    CancelledListing({"otc_listing_id": pool['otc_listing_id'], "pool_id": pool_id})
    reentrancyGuardActive.set(False)

@export
def withdraw_contribution(pool_id: str):
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, please try again."
    reentrancyGuardActive.set(True)

    # --- CHECKS (including foreign reads which are a form of read-only interaction) ---
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]

    assert pool, 'pool does not exist'
    assert funder and funder["amount_contributed"] > decimal("0.0"), \
        'no contribution to withdraw or already withdrawn.'

    can_withdraw = False
    otc_listing_failed_or_expired = False
    new_pool_status_for_effect = pool["status"] # Start with current status, may change
    auto_cancelled_otc_in_this_tx = False # Flag to track if we auto-cancelled

    if now < pool["contribution_deadline"]:
        # Early withdrawal before contribution deadline
        can_withdraw = True
    else: 
        # After contribution deadline. Withdrawal depends on pool/OTC state.
        assert pool["otc_listing_id"] or now > pool["exchange_deadline"], \
            "Withdrawal attempted in an unexpected state after contribution deadline."

        if pool["otc_listing_id"]:
            # Pool was listed on OTC, check its status on the OTC contract
            otc_contract_address = metadata['otc_contract']
            otc_contract = I.import_module(otc_contract_address)
            otc_listings_foreign = ForeignHash(foreign_contract=otc_contract_address, foreign_name='otc_listing')
            otc_offer_details = otc_listings_foreign[pool["otc_listing_id"]]

            if otc_offer_details:
                if otc_offer_details["status"] == "CANCELLED":
                    otc_listing_failed_or_expired = True
                    if new_pool_status_for_effect != "OTC_FAILED":
                        new_pool_status_for_effect = "OTC_FAILED"

                elif otc_offer_details["status"] == "OPEN" and now > pool["exchange_deadline"]:
                    # ---- FIX APPLIED HERE ----
                    # OTC listing is still OPEN but EXPIRED.
                    # The crowdfund contract (as maker of the OTC offer) must cancel it to retrieve tokens.
                    otc_contract.cancel_offer(listing_id=pool["otc_listing_id"])
                    # After successful cancellation, the pool_tokens are returned to this crowdfund contract.
                    auto_cancelled_otc_in_this_tx = True # Mark that we performed cancellation
                    # ---- END OF FIX APPLICATION ----
                    
                    otc_listing_failed_or_expired = True
                    new_pool_status_for_effect = "OTC_FAILED" 

                elif otc_offer_details["status"] == "EXECUTED":
                    assert False, "OTC deal was executed. Use withdraw_share() instead."
            else: 
                if now > pool["exchange_deadline"]:
                    otc_listing_failed_or_expired = True
                    if new_pool_status_for_effect != "OTC_FAILED":
                        new_pool_status_for_effect = "OTC_FAILED"
        
        elif not pool["otc_listing_id"] and now > pool["exchange_deadline"]:
            otc_listing_failed_or_expired = True
            if new_pool_status_for_effect != "OTC_FAILED" and new_pool_status_for_effect != "REFUNDING":
                 new_pool_status_for_effect = "OTC_FAILED"

    if otc_listing_failed_or_expired:
        can_withdraw = True
        if new_pool_status_for_effect == pool["status"] and pool["status"] not in ["OTC_FAILED", "REFUNDING"]:
             new_pool_status_for_effect = "OTC_FAILED"

    assert can_withdraw, 'Withdrawal not allowed at this stage.'

    amount_to_withdraw = funder["amount_contributed"]

    # --- EFFECTS (BEFORE INTERACTION of refunding to user) ---
    pool["amount_received"] -= amount_to_withdraw
    if pool["status"] != new_pool_status_for_effect: 
        pool["status"] = new_pool_status_for_effect
    pool_fund[pool_id] = pool
    
    if new_pool_status_for_effect == "OTC_FAILED" and pool["otc_listing_id"]:
        deal_info = otc_deal_info[pool_id]
        if deal_info and deal_info.get("status") not in ["FAILED_OR_EXPIRED", "CANCELLED", "EXECUTED"]:
             
            if auto_cancelled_otc_in_this_tx:
                deal_info["status"] = "CANCELLED" # Explicitly cancelled by this transaction
            else:
                # If not auto-cancelled here, but still failed, check foreign status for precision if needed,
                # or default to FAILED_OR_EXPIRED.
                # For simplicity, if it's failed and wasn't executed, and we didn't just cancel it,
                # it might have been cancelled by creator/operator, or truly expired without recovery attempt yet.
                # If already CANCELLED on OTC (by creator/op), that state should remain.
                otc_listings_check = ForeignHash(foreign_contract=metadata['otc_contract'], foreign_name='otc_listing')
                otc_offer_check_details = otc_listings_check[pool["otc_listing_id"]]
                if otc_offer_check_details and otc_offer_check_details['status'] == 'CANCELLED':
                    deal_info["status"] = 'CANCELLED'
                else:
                    deal_info["status"] = "FAILED_OR_EXPIRED"
            otc_deal_info[pool_id] = deal_info
            
    funder["amount_contributed"] = decimal("0.0") 
    contributor[ctx.caller, pool_id] = funder
    
    # --- INTERACTION (Refunding to user) ---
    pool_token_contract_module = I.import_module(pool["pool_token"])
    pool_token_contract_module.transfer(
        amount=amount_to_withdraw,
        to=ctx.caller
    )

    reentrancyGuardActive.set(False)

@export
def withdraw_share(pool_id: str):
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, please try again."
    reentrancyGuardActive.set(True)

    # --- CHECKS (including foreign reads) ---
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]

    assert pool, 'pool does not exist'
    assert funder and funder["amount_contributed"] > decimal("0.0"), \
        'no original contribution to claim a share for.'
    assert not funder["share_withdrawn"], 'share already withdrawn.'
    assert pool["otc_listing_id"], "OTC deal was not initiated for this pool."
    
    # pool["amount_received"] here refers to the total pool_tokens collected *before* OTC.
    # This must be positive if funder["amount_contributed"] was positive.
    assert pool["amount_received"] > decimal("0.0"), 'Initial pool amount is zero, cannot calculate share.'


    otc_listings_foreign = ForeignHash(foreign_contract=metadata['otc_contract'], foreign_name='otc_listing')
    otc_offer_details = otc_listings_foreign[pool["otc_listing_id"]]
    assert otc_offer_details, "OTC listing details not found on the exchange contract."
    assert otc_offer_details["status"] == "EXECUTED", 'OTC deal not successfully executed on the exchange contract.'

    # --- EFFECTS (BEFORE INTERACTION) ---
    # 1. Update pool status and otc_actual_received_amount if this is the first time processing execution.
    if pool["status"] != "OTC_EXECUTED":
        pool["status"] = "OTC_EXECUTED"
        pool["otc_actual_received_amount"] = otc_offer_details["take_amount"] # from foreign read
        pool_fund[pool_id] = pool # Persist change
        
        deal_info = otc_deal_info[pool_id]
        if deal_info: 
            deal_info["status"] = "EXECUTED"
            deal_info["actual_received_amount"] = pool["otc_actual_received_amount"]
            otc_deal_info[pool_id] = deal_info
    
    # Share calculation based on actual received amount (either freshly set or previously stored)
    # Ensure to use the potentially updated pool["otc_actual_received_amount"]
    actual_received_take_tokens = pool["otc_actual_received_amount"]
    # Ensure initial_pooled_amount (pool["amount_received"]) is used for the denominator
    # as it represents the total base upon which shares are calculated.
    initial_pooled_amount = pool["amount_received"] 

    amount_of_take_token_to_withdraw = (funder["amount_contributed"] * actual_received_take_tokens) / initial_pooled_amount
    assert amount_of_take_token_to_withdraw > decimal("0.0"), "Calculated share is zero or negative."

    # 2. Mark the funder's share as withdrawn
    funder["share_withdrawn"] = True 
    contributor[ctx.caller, pool_id] = funder

    # --- INTERACTION ---
    token_contract_module = I.import_module(pool["otc_take_token"]) # otc_take_token was set on listing
    token_contract_module.transfer(
        amount=amount_of_take_token_to_withdraw,
        to=ctx.caller
    )
    
    reentrancyGuardActive.set(False)

# --- Helper/View functions (Read-only, no guard needed) ---
@export
def get_pool_info(pool_id: str):
    return pool_fund[pool_id]

@export
def get_contribution_info(pool_id: str, account: str):
    return contributor[account, pool_id]

@export
def get_otc_deal_info_for_pool(pool_id: str):
    return otc_deal_info[pool_id]