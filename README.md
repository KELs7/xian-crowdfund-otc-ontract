# Xian Crowdfund-OTC Contract

This smart contract facilitates a two-stage process:
1.  **Crowdfunding:** Users can create funding pools to collect a specific fungible token (`pool_token`). Other users can contribute to these pools.
2.  **OTC Exchange:** Once a pool's contribution period ends and certain conditions (like meeting a soft cap) are met, the pool creator can list the collected `pool_token` on a designated Over-The-Counter (OTC) exchange contract to swap them for a different target token (`otc_take_token`).

Contributors can then either reclaim their original contribution if the OTC deal fails or the pool doesn't meet its goals, or claim their proportional share of the `otc_take_token` if the deal is successful.

## Contract Overview

The contract manages multiple crowdfunding pools, each with its own lifecycle:
- **Open for Contribution:** Users can contribute `pool_token`.
- **Pending OTC/OTC Listed:** After the contribution deadline, if the soft cap is met, the pool creator can list the funds on an OTC exchange.
- **OTC Executed:** If the OTC deal is successful, contributors can withdraw their share of the acquired tokens.
- **OTC Failed/Refunding:** If the OTC deal fails, is cancelled, or the soft cap isn't met, contributors can withdraw their original contributions.

## How to Use the Contract Methods

### For All Users:

#### `create_pool(description: str, pool_token: str, hard_cap: float, soft_cap: float)`
- **What it does:** Allows any user to initiate a new crowdfunding pool.
- **Capabilities:**
    - Define a `description` for the pool's purpose (up to a configured maximum length).
    - Specify the `pool_token` contract address (must be an XSC001-compliant fungible token) that will be collected.
    - Set a `hard_cap`: the maximum amount of `pool_token` that can be raised.
    - Set a `soft_cap`: the minimum amount of `pool_token` required for the pool to proceed to the OTC exchange phase. The `hard_cap` must be greater than the `soft_cap`, and the `soft_cap` must be positive.
- **Outcome:** A new pool is created with a unique `pool_id` (returned by the function). Contribution and exchange deadlines are automatically set based on contract configuration. The caller of this function becomes the `pool_creator`.
- **Event Emitted:** `PoolCreated`

#### `contribute(pool_id: str, amount: float)`
- **What it does:** Allows any user to contribute `pool_token` to an existing, active pool.
- **Capabilities:**
    - Participate in a funding pool by sending a specified `amount` of the `pool_token`.
    - **Prerequisite:** You must first `approve` this crowdfund contract to spend the `amount` of your `pool_token` by calling the `approve` method on the `pool_token`'s contract.
- **Conditions:**
    - The `pool_id` must exist.
    - The contribution must occur before the pool's `contribution_deadline`.
    - The `amount` must be positive.
    - The total contributions (including this one) must not exceed the pool's `hard_cap`.
- **Event Emitted:** `Contribution`

#### `withdraw_contribution(pool_id: str)`
- **What it does:** Allows a contributor to reclaim their contributed `pool_token` under specific circumstances.
- **Capabilities:**
    - Get your original `pool_token` contribution back if:
        1.  The pool's `contribution_deadline` has **not** yet passed.
        2.  The pool's `contribution_deadline` **has** passed, **and** one of the following is true:
            - The soft cap was not met by the `exchange_deadline` (and no OTC listing was attempted or it failed).
            - An OTC listing was created but was subsequently cancelled (either via this contract's `cancel_otc_listing_for_pool` or directly on the OTC contract if its status reflects cancellation).
            - An OTC listing was created and remained open (not executed) past the pool's `exchange_deadline`.
- **Conditions:**
    - You must have a previous, non-zero contribution to the specified `pool_id`.
    - This method cannot be used if the OTC deal for the pool was successfully executed (use `withdraw_share` instead).
- **Outcome:** Your contributed `pool_token` amount is transferred back to you. Your recorded contribution amount for this pool is set to zero.

#### `withdraw_share(pool_id: str)`
- **What it does:** Allows a contributor to claim their proportional share of the `otc_take_token` acquired through a successful OTC exchange.
- **Capabilities:**
    - Receive your portion of the tokens obtained from the OTC deal. Your share is calculated based on your original contribution relative to the total `pool_token` amount that was part of the successful OTC exchange.
- **Conditions:**
    - You must have a previous, non-zero contribution to the specified `pool_id`.
    - You must not have already withdrawn your share.
    - The OTC listing for the pool must have been successfully `EXECUTED` on the external OTC contract. This crowdfund contract verifies this by reading the status of the deal from the OTC contract.
- **Outcome:** Your calculated share of the `otc_take_token` is transferred to you. You are marked as having withdrawn your share for this pool.

### For Pool Creators:

(A "pool creator" is the user who initially called `create_pool` for a specific `pool_id`.)

#### `list_pooled_funds_on_otc(pool_id: str, otc_take_token: str, otc_total_take_amount: float)`
- **What it does:** Allows the creator of a pool to list the collected `pool_token` on the configured OTC exchange contract.
- **Capabilities:**
    - Initiate an OTC trade to swap the pooled `pool_token` for a desired `otc_take_token`.
    - You specify the contract address of the `otc_take_token` (must be XSC001-compliant) and the `otc_total_take_amount` of this token you wish to receive.
    - This crowdfund contract will first deduct an OTC maker fee (calculated based on a fee percentage read from the OTC contract) from the total `pool_token` collected. The remaining `pool_token` amount is then offered on the OTC exchange for the specified `otc_total_take_amount`.
- **Conditions:**
    - You must be the `pool_creator` for the specified `pool_id`.
    - The pool's `contribution_deadline` must have passed.
    - The pool's `exchange_deadline` must **not** have passed.
    - The total `amount_received` in the pool must be greater than or equal to its `soft_cap`.
    - The pool must not already have an active OTC listing (`otc_listing_id` must be null).
    - The `otc_total_take_amount` must be positive.
- **Outcome:** The crowdfund contract approves the OTC contract to spend the necessary amount of pooled `pool_token`. It then calls the OTC contract's `list_offer` method. A `listing_id` generated by the OTC contract is returned and stored for the pool.
- **Event Emitted:** `PoolListedOTC`

#### `cancel_otc_listing_for_pool(pool_id: str)`
- **What it does:** Allows the pool creator (or the contract operator) to attempt to cancel an active OTC listing for their pool.
- **Capabilities:**
    - Retract the pool's offer from the OTC exchange if it has not yet been executed or already cancelled on the OTC side.
- **Conditions:**
    - You must be the `pool_creator` for the `pool_id` or the contract `operator`.
    - The pool must have an `otc_listing_id` (meaning `list_pooled_funds_on_otc` was successfully called).
    - The pool's status within this contract must be `OTC_LISTED`, or `OTC_FAILED` and past the `exchange_deadline` (allowing cleanup of an expired listing).
    - Crucially, the corresponding offer on the external OTC contract must still be in an "OPEN" state. If it's already executed or cancelled on the OTC contract itself, this function will likely fail or be redundant.
- **Outcome:** If successful, this crowdfund contract calls the `cancel_offer` method on the OTC contract using the stored `otc_listing_id`. The `pool_token` (minus any fees potentially retained by the OTC contract as per its own logic) should be returned to this crowdfund contract by the OTC contract's `cancel_offer` function. The pool's status in this contract is updated (e.g., to `OTC_FAILED`).
- **Event Emitted:** `CancelledListing`

### For the Contract Operator:

(The "operator" is the address that deployed the contract, or a new address set via `change_metadata`.)

#### `change_metadata(key: str, value: Any)`
- **What it does:** Allows the contract operator to update certain global configuration parameters of the crowdfund contract.
- **Capabilities:**
    - Modify settings such as:
        - `operator`: Transfer operator privileges to a new address.
        - `otc_contract`: Change the address of the external OTC contract this crowdfund contract interacts with.
        - `description_length`: Adjust the maximum allowed length for pool descriptions.
        - `contribution_window`: Change the default duration (in time units like `datetime.DAYS`) for pool contribution periods.
        - `exchange_window`: Change the default duration for the OTC exchange period after contributions close.
- **Conditions:**
    - Only the current `operator` can call this method.

## Read-Only / View Methods

These methods allow anyone to query information from the contract without making any state changes. Depending on the blockchain, these calls might be free or incur minimal read fees.

#### `get_pool_info(pool_id: str)`
- **Returns:** A dictionary containing all details of the specified `pool_id`, such as its description, `pool_token` contract, hard and soft caps, contribution and exchange deadlines, current `amount_received`, `status`, `pool_creator`, and OTC-related information (`otc_listing_id`, `otc_take_token`, `otc_actual_received_amount`) if applicable.

#### `get_contribution_info(pool_id: str, account: str)`
- **Returns:** A dictionary detailing the contribution made by a specific `account` to a given `pool_id`. This includes `amount_contributed` (their current active contribution) and a boolean `share_withdrawn` (indicating if they've claimed proceeds from a successful OTC deal). Returns `None` if no contribution record exists.

#### `get_otc_deal_info_for_pool(pool_id: str)`
- **Returns:** A dictionary with information specifically about the OTC listing attempt for the given `pool_id`. This includes the `listing_id` on the OTC contract, the `target_take_token`, the `target_take_amount` aimed for, the `listed_pool_token_amount`, and the `status` of the deal as tracked/interpreted by this crowdfund contract (e.g., "EXECUTED", "CANCELLED_VIA_CROWDFUND", "FAILED_OR_EXPIRED"). Returns `None` if no OTC deal info is stored for the pool.

## Events

The contract emits the following events, which can be monitored by off-chain services or user interfaces to track activity:

-   **`PoolCreated`**: Fired when a new pool is created.
    -   Params: `id` (pool_id, indexed), `description`, `pool_token`, `hard_cap`, `soft_cap`, `contribution_deadline`, `exchange_deadline`.
-   **`PoolListedOTC`**: Fired when a pool's funds are successfully listed on the OTC exchange.
    -   Params: `otc_listing_id` (indexed), `pool_id`, `pool_token`, `pool_token_amount` (amount offered on OTC), `otc_take_token`, `otc_total_take_amount` (amount sought on OTC).
-   **`CancelledListing`**: Fired when an OTC listing for a pool is cancelled via this contract's `cancel_otc_listing_for_pool` method.
    -   Params: `otc_listing_id` (indexed), `pool_id`.
-   **`Contribution`**: Fired when a user contributes to a pool.
    -   Params: `pool_id` (indexed), `amount` (of this specific contribution), `pool_amount` (total in pool after this contribution).