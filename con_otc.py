random.seed()
I = importlib

# State Variables
fee = Variable()
otc_listing = Hash()
owner = Variable()
earned_fees = Hash(default_value=decimal("0.0"))
reentrancyGuardActive = Variable(default_value=False) # New state variable for re-entrancy guard

token_interface = [
    importlib.Func('transfer_from', args=('amount', 'to', 'main_account')),
    importlib.Func('transfer', args=('amount', 'to')),
    importlib.Func('balance_of', args=('address',)),
]

# Event
OfferEvent = LogEvent(
    event="Offer",
    params={
        "id":{'type':str, 'idx':True},
        "maker": {'type':str, 'idx':False},
        "taker": {'type':str, 'idx':True},
        "offer_token": {'type':str, 'idx':False},
        "offer_amount": {'type':(int, float, decimal)},
        "take_token": {'type':str, 'idx':False},
        "take_amount": {'type':(int, float, decimal)},
        "date_listed": {'type':str, 'idx':False},
        "fee": {'type':(int, float, decimal)},
        "status": {'type':str, 'idx':True}
    })

TakeOfferEvent = LogEvent(
    event="TakeOffer",
    params={
        "id":{'type':str, 'idx':True},
        "maker": {'type':str, 'idx':False},
        "taker": {'type':str, 'idx':True},
        "offer_token": {'type':str, 'idx':False},
        "offer_amount": {'type':(int, float, decimal)},
        "take_token": {'type':str, 'idx':False},
        "take_amount": {'type':(int, float, decimal)},
        "date_taken": {'type':str, 'idx':False},
        "fee": {'type':(int, float, decimal)},
        "status": {'type':str, 'idx':True}
    })

CancelOfferEvent = LogEvent(
    event="CancelOffer",
    params={
        "id":{'type':str, 'idx':True},
        "maker": {'type':str, 'idx':False},
        "taker": {'type':str, 'idx':True},
        "offer_token": {'type':str, 'idx':False},
        "offer_amount": {'type':(int, float, decimal)},
        "take_token": {'type':str, 'idx':False},
        "take_amount": {'type':(int, float, decimal)},
        "date_cancelled": {'type':str, 'idx':False},
        "fee": {'type':(int, float, decimal)},
        "status": {'type':str, 'idx':True}
    })

FeeAdjustmentEvent = (LogEvent(event="FeeAdjustment", params={"new_fee":{'type':(int, float, decimal)}}))

@construct
def init():
    owner.set(ctx.caller)
    fee.set(decimal("0.5"))
    reentrancyGuardActive.set(False) # Initialize lock state

@export
def list_offer(
    offer_token: str,
    offer_amount: float,
    take_token: str,
    take_amount: float
):
    assert not reentrancyGuardActive.get(), "Contract is busy, please try again." # Re-entrancy Guard Check
    reentrancyGuardActive.set(True) # Activate Guard

    # Checks
    assert offer_amount > decimal("0.0"), "Offer amount must be positive"
    assert take_amount > decimal("0.0"), "Take amount must be positive"

    # --- Stronger ID Generation ---
    id_components = []
    id_components.append(str(now))
    id_components.append(ctx.this)
    id_components.append(ctx.caller)
    id_components.append(ctx.signer)
    id_components.append(offer_token)
    id_components.append(str(offer_amount))
    id_components.append(take_token)
    id_components.append(str(take_amount))
    id_components.append(str(random.getrandbits(128))) # High entropy random number

    raw_id_string = ":".join(id_components)
    listing_id_generated = hashlib.sha256(raw_id_string)

    assert not otc_listing[listing_id_generated], "Generated ID not unique. This is highly unlikely; please report."
    # --- End of Stronger ID Generation ---

    # Pre-calculate fee based on current contract fee
    current_contract_fee_percent = fee.get()
    maker_fee_to_collect = offer_amount / 100 * current_contract_fee_percent

    # Import and validate tokens (Checks before effects/interactions)
    offer_token_contract_module = I.import_module(offer_token)
    take_token_contract_module = I.import_module(take_token)
    assert importlib.enforce_interface(offer_token_contract_module, token_interface), 'offer_token contract not XSC001-compliant'
    assert importlib.enforce_interface(take_token_contract_module, token_interface), 'take_token contract not XSC001-compliant'

    # Effects: Create the listing data structure first (partially, if needed, or fully if no more abort conditions before interaction)
    # In this case, we can prepare the listing object, but only store it after successful transfer.
    # However, the critical part is that the transfer_from happens before the listing is finalized in state.

    # Interaction: Transfer funds from maker
    offer_amount_balance_before_transfer = offer_token_contract_module.balance_of(address=ctx.this)

    offer_token_contract_module.transfer_from(
        amount=offer_amount + maker_fee_to_collect,
        to=ctx.this,
        main_account=ctx.caller
    )

    offer_amount_balance_after_transfer = offer_token_contract_module.balance_of(address=ctx.this)
    actual_offer_amount_received = offer_amount_balance_after_transfer - offer_amount_balance_before_transfer
    actual_offer_amount_received_without_fee = actual_offer_amount_received - maker_fee_to_collect

    current_time_for_id_and_listing = now

    # Effects (finalize state): Create the listing *after* successful transfer
    otc_listing[listing_id_generated] = {
        "maker": ctx.caller,
        "taker": None,
        "offer_token": offer_token,
        "offer_amount": actual_offer_amount_received_without_fee,
        "take_token": take_token,
        "take_amount": take_amount,
        "date_listed": current_time_for_id_and_listing, # Use consistent time
        "fee": current_contract_fee_percent, # Store the fee percent at the time of listing
        "status": "OPEN",
    }

    OfferEvent({
        "id": listing_id_generated,
        "maker": ctx.caller,
        "taker": "None",
        "offer_token": offer_token,
        "offer_amount": actual_offer_amount_received_without_fee,
        "take_token": take_token,
        "take_amount": take_amount,
        "date_listed": str(current_time_for_id_and_listing),
        "fee": current_contract_fee_percent,
        "status": "OPEN",
    })

    reentrancyGuardActive.set(False) # Deactivate Guard
    return listing_id_generated


@export
def take_offer(listing_id: str):
    assert not reentrancyGuardActive.get(), "Contract is busy, please try again." # Re-entrancy Guard Check
    reentrancyGuardActive.set(True) # Activate Guard

    # --- Checks ---
    # Retrieve offer data once and store for use
    initial_offer_state = otc_listing[listing_id]
    assert initial_offer_state, "Offer ID does not exist"
    assert initial_offer_state["status"] == "OPEN", "Offer not available"

    # Store original values from the offer before modification for calculations and events
    original_maker = initial_offer_state["maker"]
    original_offer_token = initial_offer_state["offer_token"]
    original_offer_amount = initial_offer_state["offer_amount"]
    original_take_token = initial_offer_state["take_token"]
    original_take_amount = initial_offer_state["take_amount"]
    listing_fee_percent = initial_offer_state["fee"] # Fee percent set at time of listing

    # --- Effects: Modify state BEFORE interactions ---
    # Mark offer as EXECUTED IMMEDIATELY
    current_listing_data = otc_listing[listing_id] # Get a fresh reference to modify
    current_listing_data["status"] = "EXECUTED"
    current_listing_data["taker"] = ctx.caller
    otc_listing[listing_id] = current_listing_data # Save changes

    # Calculations (based on original offer data and listing_fee_percent)
    taker_fee_payable = original_take_amount / decimal("100.0") * listing_fee_percent
    maker_fee_earned_from_listing = original_offer_amount / decimal("100.0") * listing_fee_percent

    # Update earned fees
    current_earned_for_offer_token = earned_fees[original_offer_token]
    earned_fees[original_offer_token] = current_earned_for_offer_token + maker_fee_earned_from_listing

    current_earned_for_take_token = earned_fees[original_take_token]
    earned_fees[original_take_token] = current_earned_for_take_token + taker_fee_payable

    # --- Interactions (External Calls) ---
    # 1. Taker sends their tokens (take_token + taker_fee) to the contract
    take_token_contract_instance = I.import_module(original_take_token)

    take_amount_balance_before_transfer = take_token_contract_instance.balance_of(address=ctx.this)

    take_token_contract_instance.transfer_from(
        amount=original_take_amount + taker_fee_payable,
        to=ctx.this,
        main_account=ctx.caller # The taker
    )

    take_amount_balance_after_transfer = take_token_contract_instance.balance_of(address=ctx.this)
    actual_take_amount_received = take_amount_balance_after_transfer - take_amount_balance_before_transfer
    actual_take_amount_received_without_fee = actual_take_amount_received - taker_fee_payable

    # 2. Contract sends take_tokens to the maker
    take_token_contract_instance.transfer( # Re-use imported module
        amount=actual_take_amount_received_without_fee,
        to=original_maker
    )

    # 3. Contract sends offer_tokens to the taker (ctx.caller)
    offer_token_contract_instance = I.import_module(original_offer_token)
    offer_token_contract_instance.transfer(
        amount=original_offer_amount,
        to=ctx.caller # The taker
    )

    # Event (Log using original values where appropriate, and new status)
    TakeOfferEvent({
        "id": listing_id,
        "maker": original_maker,
        "taker": ctx.caller,
        "offer_token": original_offer_token,
        "offer_amount": original_offer_amount,
        "take_token": original_take_token,
        "take_amount": actual_take_amount_received_without_fee,
        "date_taken": str(now),
        "fee": listing_fee_percent, # The fee percent for this specific offer
        "status": "EXECUTED",
    })

    reentrancyGuardActive.set(False) # Deactivate Guard


@export
def cancel_offer(listing_id: str):
    assert not reentrancyGuardActive.get(), "Contract is busy, please try again." # Re-entrancy Guard Check
    reentrancyGuardActive.set(True) # Activate Guard

    # --- Checks ---
    # Retrieve offer data once
    offer_details_to_cancel = otc_listing[listing_id]
    assert offer_details_to_cancel, "Offer ID does not exist"
    assert offer_details_to_cancel["status"] == "OPEN", "Offer can not be cancelled"
    assert offer_details_to_cancel["maker"] == ctx.caller, "Only maker can cancel offer"

    # Store original values needed for refund and event
    offer_token_to_refund_name = offer_details_to_cancel["offer_token"]
    offer_amount_to_refund_value = offer_details_to_cancel["offer_amount"]
    fee_percent_at_listing = offer_details_to_cancel["fee"] # Fee percent stored with the offer

    # --- Effects: Modify state BEFORE interactions ---
    # Mark offer as CANCELLED IMMEDIATELY
    current_listing_data_for_cancel = otc_listing[listing_id] # Get a fresh reference
    current_listing_data_for_cancel["status"] = "CANCELLED"
    otc_listing[listing_id] = current_listing_data_for_cancel # Save changes

    # Calculation for refund
    maker_fee_paid_at_listing_time = offer_amount_to_refund_value / decimal("100.0") * fee_percent_at_listing
    total_amount_to_refund_maker = offer_amount_to_refund_value + maker_fee_paid_at_listing_time

    # --- Interaction: Refund tokens to maker ---
    offer_token_contract_for_refund = I.import_module(offer_token_to_refund_name)
    offer_token_contract_for_refund.transfer(
        amount=total_amount_to_refund_maker,
        to=ctx.caller # The maker
    )

    # Event (Log using original values where appropriate, and new status)
    CancelOfferEvent({
        "id": listing_id,
        "maker": offer_details_to_cancel["maker"],
        "taker": "None", # Was None for an OPEN offer being cancelled
        "offer_token": offer_token_to_refund_name,
        "offer_amount": offer_amount_to_refund_value,
        "take_token": offer_details_to_cancel["take_token"],
        "take_amount": offer_details_to_cancel["take_amount"],
        "date_cancelled": str(now),
        "fee": fee_percent_at_listing,
        "status": "CANCELLED",
    })

    reentrancyGuardActive.set(False) # Deactivate Guard


@export
def adjust_fee(trading_fee: float):
    # This function does not make external calls before its state change,
    # but the global lock prevents it from running if a guarded operation is in progress.
    assert not reentrancyGuardActive.get(), "Contract is busy, cannot adjust fee now."
    assert ctx.caller == owner.get(), "Only owner can call this method!"
    assert decimal("0.0") <= trading_fee <= decimal("10.0"), "Fee must be between 0.0 and 10.0 percent"
    fee.set(trading_fee) # Effect
    FeeAdjustmentEvent({"new_fee": trading_fee})


@export
def withdraw(token_list: list):
    assert not reentrancyGuardActive.get(), "Contract is busy, cannot withdraw now." # Re-entrancy Guard Check
    reentrancyGuardActive.set(True) # Activate Guard

    assert ctx.caller == owner.get(), "Only owner can call this method!"

    for token_contract_name_in_list in token_list: # Renamed loop variable for clarity
        amount_to_withdraw_for_token = earned_fees[token_contract_name_in_list]
        if amount_to_withdraw_for_token > decimal("0.0"):
            # Effect first: update internal accounting before external call
            earned_fees[token_contract_name_in_list] = decimal("0.0")

            # Interaction
            token_module_to_withdraw_instance = I.import_module(token_contract_name_in_list) # Renamed for clarity
            token_module_to_withdraw_instance.transfer(
                amount=amount_to_withdraw_for_token,
                to=owner.get()
            )
            # If transfer fails, the transaction aborts, earned_fees[token] = 0.0 is rolled back.

    reentrancyGuardActive.set(False) # Deactivate Guard

@export
def view_earned_fees(token: str):
    return earned_fees[token]

@export
def view_contract_balance(token: str):
    balances = ForeignHash(foreign_contract=token, foreign_name='balances')
    token_balance = balances[ctx.this]
    return decimal(str(token_balance)) if token_balance is not None else decimal("0.0")