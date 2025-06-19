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

@export
def change_metadata(key: str, value: Any):
    assert ctx.caller == metadata['operator'], 'Only operator can set metadata!'
    metadata[key] = value

@export
def create_pool(description: str, pool_token: str, hard_cap: float, soft_cap: float):
    assert len(description) <= metadata['description_length'], f"description too long should be <{metadata['description_length']}"
    assert hard_cap > soft_cap, 'hard cap amount should be greater than soft cap amount'
    assert soft_cap > decimal("0.0"), 'soft cap must be positive'

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
        "hard_cap": hard_cap,
        "soft_cap": soft_cap,
        "amount_received": decimal("0.0"),
        "pool_creator": ctx.caller,
        "status": "OPEN_FOR_CONTRIBUTION", # "OPEN_FOR_CONTRIBUTION", "PENDING_OTC", "OTC_LISTED", "OTC_EXECUTED", "OTC_FAILED", "REFUNDING"
        "otc_listing_id": None,
        "otc_take_token": None,
        "otc_actual_received_amount": decimal("0.0") # Amount of take_token actually received
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
    pool = pool_fund[pool_id]
    assert pool, 'pool does not exist'
    assert now < pool["contribution_deadline"], 'contribution window closed.'

    assert amount > decimal("0.0"), 'contribution amount must be positive.'
    assert pool["amount_received"] + amount <= pool["hard_cap"], 'contribution exceeds hard cap.'

    # Transfer token from contributor to this contract (con_crowdfund_otc)
    I.import_module(pool["pool_token"]).transfer_from(
        amount=amount,
        to=ctx.this,
        main_account=ctx.caller
    )

    pool["amount_received"] += amount
    
    funder_info = contributor[ctx.caller, pool_id]
    if funder_info:
        funder_info["amount_contributed"] += amount
    else:
        funder_info = {"amount_contributed": amount, "share_withdrawn": False}
    
    contributor[ctx.caller, pool_id] = funder_info
    pool_fund[pool_id] = pool

    Contribution({"pool_id": pool_id, "amount": amount, "pool_amount": pool["amount_received"]})

@export
def list_pooled_funds_on_otc(pool_id: str, otc_take_token: str, otc_total_take_amount: float):
    pool = pool_fund[pool_id]
    assert pool, 'pool does not exist'
    assert ctx.caller == pool["pool_creator"], 'Only pool creator can initiate OTC listing.'
    # assert pool["status"] == "OPEN_FOR_CONTRIBUTION" or pool["status"] == "PENDING_OTC", "Pool not in correct state to list on OTC."
    assert now > pool["contribution_deadline"], 'Cannot list on OTC before contribution deadline.'
    assert now < pool["exchange_deadline"], 'Exchange window has passed for OTC listing.'
    assert pool["amount_received"] >= pool["soft_cap"], 'Soft cap not met, cannot proceed to OTC.'
    assert pool["otc_listing_id"] is None, 'OTC deal already initiated for this pool.'

    assert otc_total_take_amount > decimal("0.0"), "OTC take amount must be positive."

    # Verify the take_token contract
    take_token_contract = I.import_module(otc_take_token)
    assert I.enforce_interface(take_token_contract, token_interface), 'otc_take_token contract not XSC001-compliant'

    otc_contract = I.import_module(metadata['otc_contract'])
    
    # con_crowdfund_otc (ctx.this) is approving otc_contract_address to spend its pool_tokens
    pool_token_contract = I.import_module(pool["pool_token"])
    pool_token_contract.approve(amount=pool['amount_received'], to=metadata['otc_contract'])
    
    # con_crowdfund_otc pays maker fee for making an otc offer.
    # maker fee is deducted from pool tokens
    otc_fee_foreign = ForeignVariable(
        foreign_contract=metadata['otc_contract'],
        foreign_name='fee'
    )

    current_otc_fee_percent = otc_fee_foreign.get() # e.g., 10.0 for 10%

    # Calculate the net amount to offer such that (net_amount + fee_on_net_amount) equals total pooled funds
    # Let P = pool["amount_received"]
    # Let F_rate = current_otc_fee_percent / 100
    # We want to find X_offer_net such that X_offer_net * (1 + F_rate) = P
    # So, X_offer_net = P / (1 + F_rate)
    
    if (decimal('1.0') + current_otc_fee_percent / decimal('100.0')) == decimal('0.0'):
        # Avoid division by zero, though fee shouldn't make this factor zero.
        # This case implies -100% fee, which is unlikely/disallowed.
        assert False, "Cannot calculate offer amount with current fee yielding a zero divisor."

    net_offer_amount_for_otc = pool["amount_received"] / (decimal('1.0') + current_otc_fee_percent / decimal('100.0'))
    
    # Ensure net_offer_amount is positive, otherwise listing on OTC will fail
    assert net_offer_amount_for_otc > decimal("0.0"), "Calculated net offer amount for OTC is not positive."

    listing_id = otc_contract.list_offer(
        offer_token=pool["pool_token"],
        offer_amount=net_offer_amount_for_otc, # Pass the correctly calculated net amount
        take_token=otc_take_token,
        take_amount=otc_total_take_amount
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
        "target_take_amount": otc_total_take_amount,
        "listed_pool_token_amount": pool["amount_received"]
    }

    PoolListedOTC({
        "otc_listing_id":listing_id, 
        "pool_id": pool_id,
        "pool_token": pool["pool_token"],
        "pool_token_amount": pool["amount_received"], 
        "otc_take_token": otc_take_token,
        "otc_total_take_amount": otc_total_take_amount
    })

    return listing_id

@export
def cancel_otc_listing_for_pool(pool_id: str):
    pool = pool_fund[pool_id]
    assert pool, "Pool does not exist."
    assert ctx.caller == pool['pool_creator'] or ctx.caller == metadata['operator'], \
        "Only pool creator or operator can cancel the OTC listing."
    
    assert pool['otc_listing_id'], "No OTC listing ID found for this pool to cancel."
    # Ensure the pool is in a state where cancellation makes sense (e.g., OTC_LISTED)
    # or if it's past exchange_deadline and still OPEN on OTC.
    assert pool['status'] == "OTC_LISTED" or \
           (pool['status'] == "OTC_FAILED" and now > pool['exchange_deadline']), \
           "Pool not in a state suitable for OTC cancellation via this function, or OTC listing might not be active."

    otc_contract = I.import_module(metadata['otc_contract'])
    
    # Before calling cancel, check the foreign state to ensure it's cancellable on the OTC side
    otc_listings_foreign = ForeignHash(
        foreign_contract=metadata['otc_contract'],
        foreign_name='otc_listing' # Name of the Hash in con_otc_exchange
    )
    otc_offer_details = otc_listings_foreign[pool["otc_listing_id"]]

    assert otc_offer_details, "OTC listing details not found on the exchange contract."
    # Only allow cancellation if the offer is still OPEN on the OTC side.
    # If it's already EXECUTED or CANCELLED on OTC, this call is redundant or wrong.
    assert otc_offer_details["status"] == "OPEN", \
        f"OTC offer is not OPEN (current status: {otc_offer_details['status']}). Cannot cancel via crowdfund if already finalized on OTC."

    # The crowdfund contract (ctx.this) calls cancel_offer on the OTC contract.
    # The OTC contract's cancel_offer should verify that ctx.caller (con_crowdfund_otc) is the maker.
    otc_contract.cancel_offer(listing_id=pool['otc_listing_id'])

    # Update pool status after successful cancellation on OTC
    # The cancel_offer on OTC should have returned the tokens to con_crowdfund_otc
    pool['status'] = "OTC_FAILED" # Or "PENDING_REFUND", "REFUNDING"
    pool_fund[pool_id] = pool

    deal_info = otc_deal_info[pool_id]
    if deal_info:
        deal_info["status"] = "CANCELLED_VIA_CROWDFUND"
        otc_deal_info[pool_id] = deal_info

    CancelledListing({"otc_listing_id": pool['otc_listing_id'], "pool_id": pool_id})

@export
def withdraw_contribution(pool_id: str):
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]

    assert pool, 'pool does not exist'
    assert funder and funder["amount_contributed"] > decimal("0.0"), 'no contribution to withdraw or already withdrawn.'

    can_withdraw = False
    otc_listing_failed_or_expired = False

    # Condition 1: Contribution window still open, and OTC not yet seriously attempted
    if now < pool["contribution_deadline"]:
        can_withdraw = True
    else: # Contribution window closed or OTC process started
        assert pool["otc_listing_id"] or now > pool["contribution_deadline"], "Invalid state for this withdrawal path."

        if pool["otc_listing_id"]:
            # --- Direct Foreign Read ---
            otc_listings_foreign = ForeignHash(
                foreign_contract=metadata['otc_contract'],
                foreign_name='otc_listing'
            )
            otc_offer_details = otc_listings_foreign[pool["otc_listing_id"]]
            # --- End Direct Foreign Read ---

            if otc_offer_details:
                if otc_offer_details["status"] == "CANCELLED":
                    otc_listing_failed_or_expired = True
                elif otc_offer_details["status"] == "OPEN" and now > pool["exchange_deadline"]:
                    otc_listing_failed_or_expired = True
                # If EXECUTED, cannot withdraw contribution, must withdraw share
                elif otc_offer_details["status"] == "EXECUTED":
                    assert False, "OTC deal was executed. Use withdraw_share() instead."
            else:
                # Listing ID exists but no details found on OTC contract - could be an issue, or if OTC prunes old data.
                # For safety, if past exchange deadline, assume failure for refund.
                if now > pool["exchange_deadline"]:
                    otc_listing_failed_or_expired = True
        
        # If not listed on OTC at all, and exchange deadline has passed
        elif not pool["otc_listing_id"] and now > pool["exchange_deadline"] and pool["amount_received"] < pool["soft_cap"]:
             otc_listing_failed_or_expired = True # Soft cap not met, OTC was not attempted
        elif not pool["otc_listing_id"] and now > pool["exchange_deadline"] and pool["amount_received"] >= pool["soft_cap"]:
             # Soft cap was met, but creator didn't list it. Allow refund.
             otc_listing_failed_or_expired = True


    if otc_listing_failed_or_expired:
        can_withdraw = True
        # Optionally update local status for record keeping
        if pool["status"] not in ["OTC_FAILED", "REFUNDING"]:
            pool["status"] = "OTC_FAILED" # Or "REFUNDING"
            pool_fund[pool_id] = pool
            
            deal_info = otc_deal_info[pool_id]
            if deal_info: # Should exist if otc_listing_id exists
                deal_info["status"] = "FAILED_OR_EXPIRED"
                otc_deal_info[pool_id] = deal_info

    assert can_withdraw, 'Withdrawal not allowed at this stage.'

    amount_to_withdraw = funder["amount_contributed"]
    I.import_module(pool["pool_token"]).transfer(
        amount=amount_to_withdraw,
        to=ctx.caller
    )
    pool["amount_received"] -= amount_to_withdraw
    # Only update pool_fund if amount_received changes, to save writes if it's just a status update
    if pool["amount_received"] != pool_fund[pool_id]["amount_received"] or pool["status"] != pool_fund[pool_id]["status"]:
        pool_fund[pool_id] = pool


    funder["amount_contributed"] = decimal("0.0") # Mark as withdrawn
    contributor[ctx.caller, pool_id] = funder


@export
def withdraw_share(pool_id: str):
    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]

    assert pool, 'pool does not exist'
    assert funder and funder["amount_contributed"] > decimal("0.0"), 'no original contribution to claim a share for.'
    assert not funder["share_withdrawn"], 'share already withdrawn.'
    assert pool["otc_listing_id"], "OTC deal was not initiated for this pool."
    assert pool["amount_received"] > decimal("0.0"), 'Initial pool amount is zero, cannot calculate share.'

    # --- Direct Foreign Read ---
    # Create a ForeignHash to read from the otc_listing hash in the OTC contract
    otc_listings_foreign = ForeignHash(
        foreign_contract=metadata['otc_contract'],
        foreign_name='otc_listing' # Name of the Hash in con_otc_exchange
    )
    otc_offer_details = otc_listings_foreign[pool["otc_listing_id"]]
    # --- End Direct Foreign Read ---

    assert otc_offer_details, "OTC listing details not found on the exchange contract."
    assert otc_offer_details["status"] == "EXECUTED", 'OTC deal not successfully executed on the exchange contract.'

    # If this is the first time seeing it executed, update local state for record keeping (optional but good)
    if pool["status"] != "OTC_EXECUTED":
        pool["status"] = "OTC_EXECUTED"
        # The 'take_amount' in the OTC offer is what the maker (this crowdfund contract) received.
        pool["otc_actual_received_amount"] = otc_offer_details["take_amount"]
        pool_fund[pool_id] = pool
        
        # Update otc_deal_info as well
        deal_info = otc_deal_info[pool_id]
        if deal_info: # Should exist if otc_listing_id exists
            deal_info["status"] = "EXECUTED"
            deal_info["actual_received_amount"] = pool["otc_actual_received_amount"]
            otc_deal_info[pool_id] = deal_info

    # Calculate share based on original contribution to total pooled funds
    amount_of_take_token_to_withdraw = (funder["amount_contributed"] * pool["otc_actual_received_amount"]) / pool["amount_received"]

    assert amount_of_take_token_to_withdraw > decimal("0.0"), "Calculated share is zero or negative."

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