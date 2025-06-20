random.seed()
I = importlib

pool_fund = Hash()
otc_deal_info = Hash() # To store details about the OTC interaction for each pool
contributor = Hash() # Stores {"nominal_amount_contributed": X, "actual_amount_added": Y, "share_withdrawn": False}
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
        "hard_cap": {'type':(int, float, decimal)}, # Nominal
        "soft_cap": {'type':(int, float, decimal)}, # Nominal
        "contribution_deadline": {'type':str, 'idx':False},
        "exchange_deadline": {'type':str, 'idx':False}
    })

PoolListedOTC = LogEvent(
    event="pool_listed_on_otc", 
    params={
        "otc_listing_id":{'type':str, 'idx':True}, 
        "pool_id": {'type':str, 'idx':False},
        "pool_token": {'type':str, 'idx':False}, 
        "pool_token_amount_listed": {'type':(int, float, decimal)}, # Actual amount listed on OTC
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
        "pool_id":{'type':str, 'idx':True},
        "contributor": {'type':str, 'idx':True},
        "nominal_amount": {'type':(int, float, decimal)},
        "actual_amount_added": {'type':(int, float, decimal)},
        "total_actual_pool_tokens": {'type':(int, float, decimal)}, # Sum of actual_amount_added for the pool
        "total_nominal_pool_contributions": {'type':(int, float, decimal)} # Sum of nominal_amount for the pool
    })

@construct
def seed():
    metadata['operator'] = ctx.caller
    metadata['otc_contract'] = 'con_otc' 
    metadata['description_length'] = 200
    metadata['contribution_window'] = datetime.DAYS * 5 
    metadata['exchange_window'] = datetime.DAYS * 3
    reentrancyGuardActive.set(False)

@export
def change_metadata(key: str, value: Any):
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, cannot change metadata now."
    assert ctx.caller == metadata['operator'], 'Only operator can set metadata!'
    metadata[key] = value

@export
def create_pool(description: str, pool_token: str, hard_cap: float, soft_cap: float):
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
        "hard_cap": hard_cap, # Nominal hard cap
        "soft_cap": soft_cap, # Nominal soft cap
        "amount_received": decimal("0.0"), # Sum of actual (post-tax) tokens received
        "total_nominal_contributions": decimal("0.0"), # Sum of nominal contributions
        "pool_creator": ctx.caller,
        "status": "OPEN_FOR_CONTRIBUTION",
        "otc_listing_id": None,
        "otc_take_token": None,
        "otc_actual_received_amount": decimal("0.0") # Take tokens received from OTC
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
def contribute(pool_id: str, amount: float): # amount is nominal
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, please try again."
    reentrancyGuardActive.set(True)

    pool = pool_fund[pool_id]
    assert pool, 'pool does not exist'
    assert now < pool["contribution_deadline"], 'contribution window closed.'
    assert amount > decimal("0.0"), 'contribution amount must be positive.'
    # Check hard cap against total nominal contributions
    assert pool["total_nominal_contributions"] + amount <= pool["hard_cap"], \
        'contribution exceeds hard cap (nominal).'

    pool_token_contract_address = pool["pool_token"] 
    token_contract_module = I.import_module(pool_token_contract_address)

    # --- Interaction Part 1: Check balance before transfer ---
    balance_before_transfer = token_contract_module.balance_of(ctx.this)
    if balance_before_transfer is None: # Handle case where balance_of might return None for 0
        balance_before_transfer = decimal("0.0")

    # --- INTERACTION Part 2: Transfer pool_tokens from contributor to this contract ---
    token_contract_module.transfer_from(
        amount=amount, # Nominal amount to transfer
        to=ctx.this, 
        main_account=ctx.caller
    )

    # --- Interaction Part 3: Check balance after transfer to determine actual amount received ---
    balance_after_transfer = token_contract_module.balance_of(ctx.this)
    if balance_after_transfer is None:
         balance_after_transfer = decimal("0.0")
    
    actual_amount_added_by_this_contribution = balance_after_transfer - balance_before_transfer
    
    # It's possible for actual_amount_added to be <= amount (due to tax)
    # It should not be negative. It could be zero if tax is 100%.
    assert actual_amount_added_by_this_contribution >= decimal("0.0"), \
        "Actual amount received cannot be negative."
    # If a positive nominal amount was sent, but 0 actual tokens were added (e.g., 100% tax),
    # this might be an undesirable state for the pool if not handled.
    # For now, we allow it, but a pool creator might want to vet tokens.
    # If actual_amount_added is 0 for a non-zero nominal contribution, this funder won't get any share later.

    # --- EFFECTS (AFTER INTERACTIONS) ---
    pool["amount_received"] += actual_amount_added_by_this_contribution # Tracks sum of actual tokens
    pool["total_nominal_contributions"] += amount # Tracks sum of nominal amounts
    pool_fund[pool_id] = pool

    funder = contributor[ctx.caller, pool_id]
    if funder:
        funder["amount_contributed"] += amount # Nominal amount
        funder["actual_amount_added"] += actual_amount_added_by_this_contribution
    else:
        funder = {
            "amount_contributed": amount, # Nominal
            "actual_amount_added": actual_amount_added_by_this_contribution,
            "share_withdrawn": False
        }
    contributor[ctx.caller, pool_id] = funder

    Contribution({
        "pool_id": pool_id,
        "contributor": ctx.caller,
        "nominal_amount": amount,
        "actual_amount_added": actual_amount_added_by_this_contribution,
        "total_actual_pool_tokens": pool["amount_received"],
        "total_nominal_pool_contributions": pool["total_nominal_contributions"]
    })

    reentrancyGuardActive.set(False)

@export
def list_pooled_funds_on_otc(pool_id: str, otc_take_token: str, otc_total_take_amount: float):
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, please try again."
    reentrancyGuardActive.set(True)

    pool = pool_fund[pool_id]
    assert pool, 'pool does not exist'
    assert ctx.caller == pool["pool_creator"], 'Only pool creator can initiate OTC listing.'
    assert now > pool["contribution_deadline"], 'Cannot list on OTC before contribution deadline.'
    assert now < pool["exchange_deadline"], 'Exchange window has passed for OTC listing.'
    # Soft cap check is against total nominal contributions
    assert pool["total_nominal_contributions"] >= pool["soft_cap"], \
        'Soft cap not met (nominal), cannot proceed to OTC.'
    # Ensure there are actual tokens to list
    assert pool["amount_received"] > decimal("0.0"), \
        'No actual pool tokens available to list (possibly due to 100% tax on all contributions).'
        
    assert pool["otc_listing_id"] is None, 'OTC deal already initiated for this pool.'
    assert otc_total_take_amount > decimal("0.0"), "OTC take amount must be positive."

    take_token_contract = I.import_module(otc_take_token)
    assert I.enforce_interface(take_token_contract, token_interface), 'otc_take_token contract not XSC001-compliant'

    otc_contract = I.import_module(metadata['otc_contract'])
    pool_token_contract = I.import_module(pool["pool_token"])
    
    # Approve OTC contract to spend the *actual* amount of pool_tokens the contract holds for this pool
    # The `pool["amount_received"]` now correctly reflects the actual (post-tax) sum.
    amount_to_list_on_otc = pool['amount_received']
    pool_token_contract.approve(amount=amount_to_list_on_otc, to=metadata['otc_contract'])
    
    otc_fee_foreign = ForeignVariable(foreign_contract=metadata['otc_contract'], foreign_name='fee')
    current_otc_fee_percent = otc_fee_foreign.get()
    
    denominator = decimal('1.0') + current_otc_fee_percent / decimal('100.0')
    assert denominator != decimal('0.0'), "Cannot calculate offer amount with current fee yielding a zero divisor."
    
    # Calculate net offer amount using the actual tokens available
    net_offer_amount_for_otc = amount_to_list_on_otc / denominator
    assert net_offer_amount_for_otc > decimal("0.0"), "Calculated net offer amount for OTC is not positive."

    listing_id = otc_contract.list_offer(
        offer_token=pool["pool_token"],
        offer_amount=net_offer_amount_for_otc, # Based on actual amount_received
        take_token=otc_take_token,
        take_amount=otc_total_take_amount
    )
    assert listing_id, "Failed to get a listing ID from OTC contract."

    pool["otc_listing_id"] = listing_id
    pool["otc_take_token"] = otc_take_token
    pool["status"] = "OTC_LISTED"
    pool_fund[pool_id] = pool
    
    otc_deal_info[pool_id] = {
        "listing_id": listing_id,
        "target_take_token": otc_take_token,
        "target_take_amount": otc_total_take_amount,
        "listed_pool_token_amount": amount_to_list_on_otc # Actual amount listed
    }

    PoolListedOTC({
        "otc_listing_id":listing_id, 
        "pool_id": pool_id,
        "pool_token": pool["pool_token"],
        "pool_token_amount_listed": amount_to_list_on_otc, # Log actual amount put up for OTC
        "otc_take_token": otc_take_token,
        "otc_total_take_amount": otc_total_take_amount
    })
    
    reentrancyGuardActive.set(False)
    return listing_id

@export
def cancel_otc_listing_for_pool(pool_id: str):
    # This function's internal logic largely remains the same,
    # as it primarily interacts with the OTC contract based on listing_id.
    # The key is that the OTC listing was created with the correct (actual) amount.
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

    otc_contract = I.import_module(metadata['otc_contract'])
    otc_contract.cancel_offer(listing_id=pool['otc_listing_id'])

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

    pool = pool_fund[pool_id]
    funder_record = contributor[ctx.caller, pool_id] # Renamed for clarity

    assert pool, 'pool does not exist'
    assert funder_record and funder_record["amount_contributed"] > decimal("0.0"), \
        'no contribution to withdraw or already withdrawn (nominal check).'
    # Check if there's actual amount to withdraw for this funder
    assert funder_record["actual_amount_added"] >= decimal("0.0"), \
        'funder has no actual amount recorded to withdraw.'
        
    can_withdraw = False
    otc_listing_failed_or_expired = False
    new_pool_status_for_effect = pool["status"] 
    auto_cancelled_otc_in_this_tx = False

    if now < pool["contribution_deadline"]:
        can_withdraw = True
    else: 
        # After contribution deadline. Withdrawal depends on pool/OTC state.
        # This includes scenarios: soft cap not met, OTC listed & failed/expired, creator never listed.
        
        # Check if soft cap (nominal) was met if we are past contribution deadline
        # and no OTC listing was attempted or relevant.
        if not pool["otc_listing_id"] and pool["total_nominal_contributions"] < pool["soft_cap"]:
             otc_listing_failed_or_expired = True # Treat as a form of failure allowing withdrawal
             if new_pool_status_for_effect != "REFUNDING": # A more specific status might be "REFUNDING_SOFT_CAP_FAIL"
                 new_pool_status_for_effect = "REFUNDING" # Or "OTC_FAILED" if preferred generic term
        
        elif pool["otc_listing_id"]:
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
                    otc_contract.cancel_offer(listing_id=pool["otc_listing_id"])
                    auto_cancelled_otc_in_this_tx = True
                    otc_listing_failed_or_expired = True
                    new_pool_status_for_effect = "OTC_FAILED" 
                elif otc_offer_details["status"] == "EXECUTED":
                    assert False, "OTC deal was executed. Use withdraw_share() instead."
            else: # OTC listing ID exists in pool, but not found on OTC contract (should be rare)
                if now > pool["exchange_deadline"]: # If past exchange window, assume failure
                    otc_listing_failed_or_expired = True
                    if new_pool_status_for_effect != "OTC_FAILED":
                         new_pool_status_for_effect = "OTC_FAILED"
        
        elif not pool["otc_listing_id"] and now > pool["exchange_deadline"]: # Creator never listed, and all windows passed
            otc_listing_failed_or_expired = True
            if new_pool_status_for_effect not in ["OTC_FAILED", "REFUNDING"]:
                 new_pool_status_for_effect = "OTC_FAILED" # Or REFUNDING

    if otc_listing_failed_or_expired:
        can_withdraw = True
        if pool["status"] not in ["OTC_FAILED", "REFUNDING"] and \
           new_pool_status_for_effect != pool["status"]:
            pass # new_pool_status_for_effect is already set
        elif pool["status"] not in ["OTC_FAILED", "REFUNDING"]: # If status wasn't changed by specific logic above
            new_pool_status_for_effect = "OTC_FAILED" # Default to OTC_FAILED

    assert can_withdraw, 'Withdrawal not allowed at this stage.'

    # Amount to refund is the actual amount this funder's contribution added to the pool
    amount_to_refund_to_user = funder_record["actual_amount_added"]
    nominal_amount_being_withdrawn = funder_record["amount_contributed"]

    # If amount_to_refund_to_user is 0 (e.g., 100% tax and they were the only one, or their part was 0),
    # then no tokens are transferred, but state is cleaned up.
    if amount_to_refund_to_user < decimal("0.0"): amount_to_refund_to_user = decimal("0.0") # Safety

    # --- EFFECTS ---
    pool["amount_received"] -= amount_to_refund_to_user # Decrease actual sum
    pool["total_nominal_contributions"] -= nominal_amount_being_withdrawn # Decrease nominal sum
    
    if pool["status"] != new_pool_status_for_effect: 
        pool["status"] = new_pool_status_for_effect
    pool_fund[pool_id] = pool
    
    if new_pool_status_for_effect == "OTC_FAILED" and pool["otc_listing_id"]:
        deal_info = otc_deal_info[pool_id]
        if deal_info and deal_info.get("status") not in ["FAILED_OR_EXPIRED", "CANCELLED", "EXECUTED"]:
            if auto_cancelled_otc_in_this_tx: deal_info["status"] = "CANCELLED"
            else: deal_info["status"] = "FAILED_OR_EXPIRED" # Or check foreign for more precision
            otc_deal_info[pool_id] = deal_info
            
    funder_record["actual_amount_added"] = decimal("0.0") 
    funder_record["amount_contributed"] = decimal("0.0") # Zero out nominal contribution as well
    contributor[ctx.caller, pool_id] = funder_record
    
    # --- INTERACTION ---
    if amount_to_refund_to_user > decimal("0.0"):
        pool_token_contract_module = I.import_module(pool["pool_token"])
        pool_token_contract_module.transfer(
            amount=amount_to_refund_to_user,
            to=ctx.caller
        )

    reentrancyGuardActive.set(False)

@export
def withdraw_share(pool_id: str):
    assert not reentrancyGuardActive.get(), "Crowdfund contract is busy, please try again."
    reentrancyGuardActive.set(True)

    pool = pool_fund[pool_id]
    funder = contributor[ctx.caller, pool_id]

    assert pool, 'pool does not exist'
    assert funder and funder["amount_contributed"] > decimal("0.0"), \
        'no original nominal contribution to claim a share for.'
    assert not funder["share_withdrawn"], 'share already withdrawn.'
    assert pool["otc_listing_id"], "OTC deal was not initiated for this pool."
    
    # Check total_nominal_contributions for share calculation
    total_nominal_contributions_for_pool = pool["total_nominal_contributions"]
    assert total_nominal_contributions_for_pool > decimal("0.0"), \
        'Total nominal contributions for the pool is zero, cannot calculate share.'

    otc_listings_foreign = ForeignHash(foreign_contract=metadata['otc_contract'], foreign_name='otc_listing')
    otc_offer_details = otc_listings_foreign[pool["otc_listing_id"]]
    assert otc_offer_details, "OTC listing details not found on the exchange contract."
    assert otc_offer_details["status"] == "EXECUTED", 'OTC deal not successfully executed on the exchange contract.'

    if pool["status"] != "OTC_EXECUTED":
        pool["status"] = "OTC_EXECUTED"
        pool["otc_actual_received_amount"] = otc_offer_details["take_amount"] 
        pool_fund[pool_id] = pool 
        
        deal_info = otc_deal_info[pool_id]
        if deal_info: 
            deal_info["status"] = "EXECUTED"
            deal_info["actual_received_amount"] = pool["otc_actual_received_amount"]
            otc_deal_info[pool_id] = deal_info
    
    actual_received_take_tokens_by_pool = pool["otc_actual_received_amount"]
    
    # Share calculation based on nominal contribution relative to total nominal contributions
    numerator = funder["amount_contributed"] * actual_received_take_tokens_by_pool
    amount_of_take_token_to_withdraw = numerator / total_nominal_contributions_for_pool
    
    assert amount_of_take_token_to_withdraw >= decimal("0.0"), "Calculated share is negative." # Can be 0 if funder's nominal was tiny or total take was tiny

    funder["share_withdrawn"] = True 
    contributor[ctx.caller, pool_id] = funder

    if amount_of_take_token_to_withdraw > decimal("0.0"):
        token_contract_module = I.import_module(pool["otc_take_token"])
        token_contract_module.transfer(
            amount=amount_of_take_token_to_withdraw,
            to=ctx.caller
        )
    
    reentrancyGuardActive.set(False)

# --- Helper/View functions ---
@export
def get_pool_info(pool_id: str):
    return pool_fund[pool_id]

@export
def get_contribution_info(pool_id: str, account: str):
    return contributor[account, pool_id]

@export
def get_otc_deal_info_for_pool(pool_id: str):
    return otc_deal_info[pool_id]