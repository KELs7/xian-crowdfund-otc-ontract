random.seed()
I = importlib

# State Variables
fee = Variable()
otc_listing = Hash()
owner = Variable()
earned_fees = Hash(default_value=decimal("0.0"))

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
        "fee": {'type':(int, float, decimal)},
        "status": {'type':str, 'idx':True}
    })

FeeAdjustmentEvent = (LogEvent(event="FeeAdjustment", params={"new_fee":{'type':(int, float, decimal)}}))

@construct
def init():
    owner.set(ctx.caller)
    fee.set(decimal("0.5"))

@export
def list_offer(
    offer_token: str,
    offer_amount: float,
    take_token: str,
    take_amount: float 
):
    assert offer_amount > decimal("0.0"), "Offer amount must be positive"
    assert take_amount > decimal("0.0"), "Take amount must be positive"
    listing_id = hashlib.sha256(str(now) + str(random.randrange(99)))
    assert not otc_listing[listing_id], "Generated ID not unique. Try again"
    maker_fee = offer_amount / 100 * fee.get()
    offer_token_contract = I.import_module(offer_token)
    take_token_contract = I.import_module(take_token)
    assert importlib.enforce_interface(offer_token_contract, token_interface), 'token contract not XSC001-compliant'
    assert importlib.enforce_interface(take_token_contract, token_interface), 'token contract not XSC001-compliant'

    offer_token_contract.transfer_from(
        amount=offer_amount + maker_fee,
        to=ctx.this,
        main_account=ctx.caller
    )

    otc_listing[listing_id] = {
        "maker": ctx.caller,
        "taker": None,
        "offer_token": offer_token,
        "offer_amount": offer_amount,
        "take_token": take_token,
        "take_amount": take_amount,
        "fee": fee.get(),
        "status": "OPEN",
    }

    OfferEvent({
        "id": listing_id,
        "maker": ctx.caller,
        "taker": "None",
        "offer_token": offer_token,
        "offer_amount": offer_amount,
        "take_token": take_token,
        "take_amount": take_amount,
        "fee": fee.get(),
        "status": "OPEN",
    })
    return listing_id


@export
def take_offer(listing_id: str):
    offer = otc_listing[listing_id]
    assert offer, "Offer ID does not exist"
    assert offer["status"] == "OPEN", "Offer not available"

    offer_fee_percent = offer["fee"] 
    taker_fee = offer["take_amount"] / decimal("100.0") * offer_fee_percent
    maker_fee = offer["offer_amount"] / decimal("100.0") * offer_fee_percent

    I.import_module(offer["take_token"]).transfer_from(
        amount=offer["take_amount"] + taker_fee,
        to=ctx.this,
        main_account=ctx.caller
    )

    current_maker_fee_earned = earned_fees[offer["offer_token"]]
    earned_fees[offer["offer_token"]] = current_maker_fee_earned + maker_fee

    current_taker_fee_earned = earned_fees[offer["take_token"]]
    earned_fees[offer["take_token"]] = current_taker_fee_earned + taker_fee

    I.import_module(offer["take_token"]).transfer(
        amount=offer["take_amount"],
        to=offer["maker"]
    )
    I.import_module(offer["offer_token"]).transfer(
        amount=offer["offer_amount"],
        to=ctx.caller
    )

    offer["status"] = "EXECUTED"
    offer["taker"] = ctx.caller
    otc_listing[listing_id] = offer

    TakeOfferEvent({
        "id": listing_id,
        "maker": offer["maker"],
        "taker": ctx.caller,
        "offer_token": offer["offer_token"],
        "offer_amount": offer["offer_amount"],
        "take_token": offer["take_token"],
        "take_amount": offer["take_amount"],
        "fee": offer["fee"],
        "status": "EXECUTED",
    })


@export
def cancel_offer(listing_id: str):
    offer = otc_listing[listing_id]
    assert offer, "Offer ID does not exist"
    assert offer["status"] == "OPEN", "Offer can not be cancelled"
    assert offer["maker"] == ctx.caller, "Only maker can cancel offer"

    maker_fee = offer["offer_amount"] / decimal("100.0") * offer["fee"]
    amount_to_refund = offer["offer_amount"] + maker_fee

    I.import_module(offer["offer_token"]).transfer(
        amount=amount_to_refund,
        to=ctx.caller
    )

    offer["status"] = "CANCELLED"
    otc_listing[listing_id] = offer

    CancelOfferEvent({
        "id": listing_id,
        "maker": offer["maker"],
        "taker": "None",
        "offer_token": offer["offer_token"],
        "offer_amount": offer["offer_amount"],
        "take_token": offer["take_token"],
        "take_amount": offer["take_amount"],
        "fee": offer["fee"],
        "status": "CANCELLED",
    })


@export
def adjust_fee(trading_fee: float):
    assert ctx.caller == owner.get(), "Only owner can call this method!"
    assert decimal("0.0") <= trading_fee <= decimal("10.0"), "Fee must be between 0.0 and 10.0 percent"
    fee.set(trading_fee)
    FeeAdjustmentEvent({"new_fee": trading_fee})


@export
def withdraw(token_list: list):
    assert ctx.caller == owner.get(), "Only owner can call this method!"

    for token in token_list:
        amount_to_withdraw = earned_fees[token]
        if amount_to_withdraw > decimal("0.0"):
            I.import_module(token).transfer(
                amount=amount_to_withdraw,
                to=owner.get()
            )
            earned_fees[token] = decimal("0.0")

@export
def view_earned_fees(token: str):
    return earned_fees[token]

@export
def view_contract_balance(token: str):
    balances = ForeignHash(foreign_contract=token, foreign_name='balances')
    token_balance = balances[ctx.this]
    return decimal(str(token_balance)) if token_balance is not None else decimal("0.0")