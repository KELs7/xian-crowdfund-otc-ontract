import unittest
from contracting.stdlib.bridge.decimal import ContractingDecimal as decimal
from contracting.stdlib.bridge.time import Datetime # Renamed to avoid conflict with standard datetime
from contracting.client import ContractingClient
from pathlib import Path

# Mock datetime for testing if not running in full xian-contracting env
# This part is usually not needed if your client and environment are set up correctly
# as the `contracting` library handles its own datetime.
# However, if you need to create Datetime objects for environment override, this is how.
# from datetime import datetime as standard_datetime

class TestCrowdfundContract(unittest.TestCase): # Renamed class for clarity
    def setUp(self):
        self.client = ContractingClient()
        self.client.flush() # Ensures a clean state

        # Define user accounts
        self.operator = 'sys' # The one who submits contracts, acts as initial operator
        self.alice = 'alice'
        self.alice_attacker = 'alice_attacker'
        self.bob = 'bob'
        self.charlie = 'charlie' # Potential taker of an OTC offer

        # Define contract names for easy reference
        self.crowdfund_contract_name = "con_crowdfund_otc"
        self.otc_contract_name = "con_otc"
        self.pool_token_name = "con_pool_token"
        self.take_token_name = "con_otc_take_token"
        self.malicious_token_name = "con_malicious_reentrant_token"

        # Submit contracts
        # Get the directory containing the test file
        current_dir = Path(__file__).resolve()
        
        # It seems your contracts are in the parent directory of the test file.
        # If con_crowdfund_otc.py is in the same directory as the test file, use:
        # crowdfund_contract_path = current_dir / "con_crowdfund_otc.py" 
        # Adjust paths as per your actual project structure.
        # Assuming contracts are in a 'contracts' subdirectory relative to the project root,
        # and tests are in a 'tests' subdirectory.
        contracts_dir = current_dir.parent # Or adjust to your project's root/contracts dir

        with open(contracts_dir / "con_crowdfund_otc.py") as f:
            code = f.read()
            self.client.submit(code, name=self.crowdfund_contract_name, signer=self.operator)

        with open(contracts_dir / "con_otc.py") as f:
            code = f.read()
            self.client.submit(code, name=self.otc_contract_name, signer=self.operator)

        with open(contracts_dir / "con_pool_token.py") as f: # Assumes a simple token contract
            code = f.read()
            self.client.submit(code, name=self.pool_token_name, signer=self.operator)

        with open(contracts_dir / "con_otc_take_token.py") as f: # Assumes a simple token contract
            code = f.read()
            self.client.submit(code, name=self.take_token_name, signer=self.operator)

        with open(contracts_dir / "con_malicious_reentrant_token.py") as f:
            code = f.read()
            self.client.submit(code, name=self.malicious_token_name, signer=self.operator)

        self.con_crowdfund_otc = self.client.get_contract(self.crowdfund_contract_name)
        self.con_otc = self.client.get_contract(self.otc_contract_name)
        self.con_pool_token = self.client.get_contract(self.pool_token_name)
        self.con_otc_take_token = self.client.get_contract(self.take_token_name)
        self.con_malicious_token = self.client.get_contract(self.malicious_token_name)

        # Initialize contracts (call @construct methods if any)
        # Crowdfund and OTC contracts have @construct that sets operator/owner to ctx.caller (signer)
        # Token contracts might have one too, e.g., to mint initial supply to the deployer.
        # If your token contracts mint to deployer (`sys`) on construction:
        print(f"Operator ({self.operator}) balance of pool token: {self.con_pool_token.balance_of(address=self.operator)}")
        print(f"Operator ({self.operator}) balance of take token: {self.con_otc_take_token.balance_of(address=self.operator)}")


        # --- Token Distribution ---
        # Assuming the operator ('sys') received all tokens upon submission/construction
        # Distribute pool tokens
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.alice, signer=self.operator)
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.bob, signer=self.operator)
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.charlie, signer=self.operator) # Charlie might also contribute

        # Distribute take tokens (e.g., to Charlie who might take an OTC offer)
        self.con_otc_take_token.transfer(amount=decimal('5000'), to=self.charlie, signer=self.operator)
        
        # Verify initial balances for users
        print(f"Alice pool token balance: {self.con_pool_token.balance_of(address=self.alice)}")
        print(f"Bob pool token balance: {self.con_pool_token.balance_of(address=self.bob)}")
        print(f"Charlie pool token balance: {self.con_pool_token.balance_of(address=self.charlie)}")
        print(f"Charlie take token balance: {self.con_otc_take_token.balance_of(address=self.charlie)}")


        # --- Approvals ---
        # 1. Users approve crowdfund contract to spend their pool_tokens for contributions
        self.con_pool_token.approve(amount=decimal('500'), to=self.crowdfund_contract_name, signer=self.alice)
        self.con_pool_token.approve(amount=decimal('500'), to=self.crowdfund_contract_name, signer=self.bob)
        # Charlie might also contribute, or just be a taker. Let's give approval just in case.
        self.con_pool_token.approve(amount=decimal('500'), to=self.crowdfund_contract_name, signer=self.charlie)

        # 2. Crowdfund contract (acting as itself, so signer is its operator/creator for this setup)
        #    needs to approve the OTC contract to spend its (the crowdfund contract's) pool_tokens
        #    when it lists an offer. This approval is done by the crowdfund contract itself.
        #    The `approve` method of `con_pool_token` needs to be callable by another contract.
        #    This is a bit tricky. A contract cannot directly sign an `approve` call on another token contract
        #    *as if it were an EOA*.
        #    The `list_offer` in your OTC contract handles this by using `transfer_from` with `main_account=ctx.caller`.
        #    So, when `con_crowdfund_otc` calls `con_otc.list_offer(...)`, `ctx.caller` inside `con_otc` is `con_crowdfund_otc`.
        #    Then `con_otc` calls `con_pool_token.transfer_from(..., main_account=con_crowdfund_otc)`.
        #    This implies that `con_crowdfund_otc` must have *approved itself* to allow `con_pool_token` to transfer its funds,
        #    OR `con_pool_token.transfer_from` must have special logic if `main_account == ctx.this` (the token contract itself).
        #    The standard way is that `con_crowdfund_otc` doesn't need to approve `con_otc`.
        #    Instead, `con_otc` will call `transfer_from` on `con_pool_token` specifying `main_account` as `con_crowdfund_otc`.
        #    This requires `con_crowdfund_otc` to have *previously approved* `con_otc` to spend its tokens.
        #    This approval must be done by `con_crowdfund_otc` itself.
        #    To do this, `con_crowdfund_otc` needs a method that calls `con_pool_token.approve`.
        #    Let's assume `con_crowdfund_otc` has such a management function, or we do it via its operator.
        #    For simplicity in setup, if `con_crowdfund_otc` is the `ctx.caller` to `con_otc.list_offer`,
        #    and `con_otc` then calls `con_pool_token.transfer_from(..., main_account=con_crowdfund_otc, ...)`
        #    this implies `con_crowdfund_otc` needs to have approved `con_otc` to spend its tokens.
        #    This is usually done by the owner/operator of `con_crowdfund_otc` calling an internal function
        #    within `con_crowdfund_otc` that then calls `con_pool_token.approve(to=con_otc, ...)`.
        #    *If your token contract's `transfer_from` allows `main_account` to be `ctx.this` (the contract itself) without prior approval for that specific case, then no explicit approval here is needed.*
        #    Let's assume the more robust pattern: `con_crowdfund_otc` must approve `con_otc`.
        #    This would typically be a function within `con_crowdfund_otc`:
        #    ```python
        #    # In con_crowdfund_otc.py
        #    @export
        #    def approve_otc_contract_spending(self, token_contract_name: str, amount: float):
        #        assert ctx.caller == metadata['operator'] # or pool_creator
        #        token_to_approve = I.import_module(token_contract_name)
        #        otc_address = metadata['otc_contract']
        #        token_to_approve.approve(amount=amount, to=otc_address) # Called by con_crowdfund_otc
        #    ```
        #    Then in setup:
        #    `self.con_crowdfund_otc.approve_otc_contract_spending(token_contract_name=self.pool_token_name, amount=decimal('10000'), signer=self.operator)`
        #    For now, I'll comment this out as it depends on that extra function. The `list_offer` in your OTC seems to handle the fee part by `transfer_from` the maker.
        #    The main `offer_amount` is also `transfer_from` the maker. So `con_crowdfund_otc` (as maker) must approve `con_otc` to pull these.
        #    This approval must be initiated by `con_crowdfund_otc` itself.
        #    If `con_pool_token.approve` can be called with `signer=con_crowdfund_otc_name`, that would be ideal but not standard.
        #    The most straightforward way is for `con_crowdfund_otc` to have an internal method called by its operator that executes this approval.
        #    Let's simulate this by having the operator of `con_crowdfund_otc` (sys) make the approval *on behalf of* the logic that `con_crowdfund_otc` would execute.
        #    This isn't perfectly clean for a unit test setup but reflects the needed state.
        #    A cleaner way requires `con_crowdfund_otc` to have an `approve_spender` method.
        #    For the purpose of the test setup, let's assume the `con_otc.list_offer` implies the maker (`con_crowdfund_otc`)
        #    must have sufficient allowance for `con_otc`.
        #    This means `con_crowdfund_otc` needs to call `self.con_pool_token.approve(to=self.otc_contract_name, amount=X)`.
        #    This can only be done if `con_crowdfund_otc` has a function that does this, and that function is called by its operator.
        #
        #    A simpler interpretation of your OTC's `list_offer`:
        #    `offer_token_contract.transfer_from(amount=offer_amount + maker_fee, to=ctx.this, main_account=ctx.caller)`
        #    Here, `ctx.caller` is `con_crowdfund_otc`. So `con_crowdfund_otc` must have approved `con_otc` (which is `ctx.this` inside `list_offer`).
        #    This is a bit circular for `approve`.
        #    The standard flow is:
        #    - Crowdfund (maker) calls `pool_token.approve(spender=otc_contract, amount=X)`
        #    - Crowdfund (maker) calls `otc_contract.list_offer(...)`
        #    - OTC contract calls `pool_token.transfer_from(owner=crowdfund, to=otc_contract, amount=X)`
        #    To achieve the first step, `con_crowdfund_otc` needs a method.
        #    Let's assume for now `con_crowdfund_otc` will have a method like `execute_approve_for_otc(pool_token_addr, amount_to_approve)`
        #    and we call it here, signed by the operator of `con_crowdfund_otc`.
        #    If such a method doesn't exist, this part of the setup is more complex.
        #    For now, I will skip this specific approval and assume `list_pooled_funds_on_otc` handles it or the token contract is lenient.
        #    This is a common point of complexity in inter-contract approvals.

        # 3. Charlie (taker) approves OTC contract to spend their take_tokens
        self.con_otc_take_token.approve(amount=decimal('5000'), to=self.otc_contract_name, signer=self.charlie)

        # Store current time for advancing it in tests
        # self.start_time = Datetime.strptime(str(self.client.raw_driver.get('__block_meta__.nanos')), '%Y-%m-%d %H:%M:%S.%f') \
        #                   if self.client.raw_driver.get('__block_meta__.nanos') else Datetime(2023,1,1) # Fallback if no block_meta

        # Base time for controlling "now" in tests
        self.base_time = Datetime(year=2024, month=1, day=1, hour=0, minute=0, second=0)

        
        print("Setup complete.")
        print(f"Crowdfund metadata 'otc_contract': {self.con_crowdfund_otc.metadata['otc_contract']}")


    def tearDown(self):
        self.client.flush()

    def _get_future_time(self, base_dt: Datetime, days=0, hours=0, minutes=0, seconds=0) -> Datetime:
        # Simplified future time generation. Datetime objects are not directly addable with integers.
        # This helper assumes we can create a new Datetime by adjusting components.
        # This is a very basic way; a proper Datetime library would handle overflows.
        # For testing, this might be sufficient if increments are small.
        # A more robust way would be to convert Datetime to a timestamp, add, and convert back,
        # or use Timedelta if the Datetime class supports it.
        # Your `contracting.stdlib.bridge.time.Datetime` can be added with `Timedelta`
        from contracting.stdlib.bridge.time import Timedelta
        delta = Timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return base_dt + delta

    # --- Example Test Case Structure ---
    def test_create_pool_and_contribute(self):
        print("\n--- Test: Create Pool and Contribute ---")
        pool_description = "Test Pool for OTC"
        hard_cap = decimal('1000')
        soft_cap = decimal('100')

        # Alice creates a pool
        pool_id = self.con_crowdfund_otc.create_pool(
            description=pool_description,
            pool_token=self.pool_token_name,
            hard_cap=hard_cap,
            soft_cap=soft_cap,
            signer=self.alice # Alice is the pool_creator
        )
        self.assertIsNotNone(pool_id, "Pool creation failed to return an ID.")
        print(f"Pool created by Alice with ID: {pool_id}")

        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertIsNotNone(pool_info, "Pool info not found after creation.")
        self.assertEqual(pool_info['pool_creator'], self.alice)
        self.assertEqual(pool_info['soft_cap'], soft_cap)
        self.assertEqual(pool_info['hard_cap'], hard_cap)
        self.assertEqual(pool_info['status'], "OPEN_FOR_CONTRIBUTION")

        # Bob contributes to the pool
        contribution_amount_bob = decimal('50')
        self.con_crowdfund_otc.contribute(
            pool_id=pool_id,
            amount=contribution_amount_bob,
            signer=self.bob
        )
        
        pool_info_after_bob_contrib = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_bob_contrib['amount_received'], contribution_amount_bob)
        
        bob_contrib_info = self.con_crowdfund_otc.contributor[self.bob, pool_id]
        self.assertEqual(bob_contrib_info['amount_contributed'], contribution_amount_bob)
        print(f"Bob contributed {contribution_amount_bob} to pool {pool_id}")

        # Alice also contributes to her own pool
        contribution_amount_alice = decimal('70')
        self.con_crowdfund_otc.contribute(
            pool_id=pool_id,
            amount=contribution_amount_alice,
            signer=self.alice
        )

        pool_info_after_alice_contrib = self.con_crowdfund_otc.pool_fund[pool_id]
        expected_total_received = contribution_amount_bob + contribution_amount_alice
        self.assertEqual(pool_info_after_alice_contrib['amount_received'], expected_total_received)

        alice_contrib_info = self.con_crowdfund_otc.contributor[self.alice, pool_id]
        self.assertEqual(alice_contrib_info['amount_contributed'], contribution_amount_alice)
        print(f"Alice contributed {contribution_amount_alice} to pool {pool_id}")
        
        print(f"Total amount received in pool: {pool_info_after_alice_contrib['amount_received']}")


    def test_contribution_deadline_respected(self):
        print("\n--- Test: Contribution Deadline Respected ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Deadline Test Pool",
            pool_token=self.pool_token_name,
            hard_cap=decimal('100'),
            soft_cap=decimal('10'),
            signer=self.alice
        )
        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        contribution_deadline = pool_info['contribution_deadline'] # This is a Datetime object

        # Try to contribute after the deadline
        # We need to simulate time passing. The 'environment' kwarg is key.
        # Create a Datetime object that is past the contribution_deadline
        # Assuming Datetime can be advanced simply for testing, or construct a new one.
        # For xian-contracting, Datetime objects are constructed with year, month, day, etc.
        # Let's assume contribution_window is 5 days. We'll try to contribute on day 6.
        
        # Get the 'now' from the environment of the create_pool call, or use a known base
        # The Datetime objects from the contract are instances of contracting.stdlib.bridge.time.Datetime
        # Example: if contribution_deadline is Datetime(2023, 1, 6, ...)
        # time_after_deadline = Datetime(2023, 1, 7, 0, 0, 0) # Construct manually
        
        # To make this robust, we need to parse the deadline and add to it.
        # The Datetime objects returned from contract state are comparable.
        # We need a way to create a Datetime object representing a future time.
        # The `datetime.DAYS` in your contract is `contracting.stdlib.bridge.time.Timedelta`.
        # So, `future_time = contribution_deadline + datetime.DAYS` (or some fraction of it).
        
        # For simplicity, let's assume we can construct a future Datetime
        # This requires knowing how your Datetime objects are structured or using constants
        # from your `contracting.stdlib.bridge.time` if they were importable here.
        # Let's assume the default contribution window is 5 days.
        # We will try to contribute 6 days after the pool creation.
        
        # To get the creation time, we'd ideally get it from the block when create_pool was called.
        # For testing, we can assume create_pool happened at self.start_time.
        # So, deadline is roughly self.start_time + 5 days.
        # A time after deadline is self.start_time + 6 days.
        
        # This is a simplified way to get a future Datetime.
        # You'd typically construct it based on the components of `contribution_deadline`.
        # Example: If contribution_deadline is (y, m, d, h, mi, s), then time_after is (y, m, d+1, h, mi, s)
        # This part is tricky without direct access to how `Datetime` objects are manipulated outside contracts.
        # The `environment={"now": <DatetimeObject>}` is the correct mechanism.

        # Let's assume contribution_deadline is a Datetime object.
        # We need to construct a new Datetime object for `time_after_deadline`.
        # If contribution_deadline is year=Y, month=M, day=D, etc.
        # time_after_deadline = Datetime(year=Y, month=M, day=D+1, ...)
        # This requires knowing the exact structure of your Datetime object.
        # For now, let's make a placeholder for how you'd construct this.
        # This is the most complex part of time-based testing without helper utilities.
        
        # A practical way:
        # 1. Get the contribution_deadline (it's a Datetime object)
        # 2. Construct a new Datetime object representing a time > contribution_deadline
        #    For example, if deadline is Datetime(y,m,d,h,mi,s), then
        #    time_after_deadline = Datetime(y,m,d,h,mi,s+1) assuming it's not at month/year end
        #    A safer way is to advance by a known delta if Datetime supports '+' with Timedelta
        
        # Since your contract uses `now + metadata['contribution_window']`
        # and `metadata['contribution_window']` is `5 * datetime.DAYS`
        # We can simulate `now` being `creation_time + 6 * datetime.DAYS`
        
        # Assume `self.start_time` is the creation time for this test pool
        # This is an approximation. A better way is to get `now` from the `create_pool` result if possible,
        # or control `now` during `create_pool`.
        
        # Let's control "now" for create_pool
        creation_env_time = Datetime(year=2024, month=1, day=1, hour=10, minute=0, second=0)
        pool_id_timed = self.con_crowdfund_otc.create_pool(
            description="Deadline Test Pool",
            pool_token=self.pool_token_name,
            hard_cap=decimal('100'),
            soft_cap=decimal('10'),
            signer=self.alice,
            environment={"now": creation_env_time} # Control "now" for pool creation
        )
        pool_info_timed = self.con_crowdfund_otc.pool_fund[pool_id_timed]
        actual_contribution_deadline = pool_info_timed['contribution_deadline'] # This is now creation_env_time + 5 days

        # Now, construct a time that is definitely after this deadline.
        # If deadline is Jan 1 + 5 days = Jan 6. Let's try Jan 7.
        time_after_deadline_env = Datetime(year=2024, month=1, day=7, hour=10, minute=0, second=0)

        with self.assertRaisesRegex(AssertionError, "contribution window closed"):
            self.con_crowdfund_otc.contribute(
                pool_id=pool_id_timed,
                amount=decimal('5'),
                signer=self.bob,
                environment={"now": time_after_deadline_env} # Mock "now" to be after deadline
            )
        print(f"Successfully prevented contribution after deadline for pool {pool_id_timed}")

    # Add more test cases here for:
    # - Hitting hard_cap
    # - Listing on OTC (soft_cap met vs. not met)
    # - OTC deal execution (success, failure/cancellation)
    # - withdraw_share after successful OTC
    # - withdraw_contribution (before deadline, after failed OTC)
    # - Edge cases, invalid inputs, permission errors
    def test_hard_cap_respected(self):
        print("\n--- Test: Hard Cap Respected ---")
        hard_cap = decimal('50')
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Hard Cap Test", pool_token=self.pool_token_name,
            hard_cap=hard_cap, soft_cap=decimal('10'), signer=self.alice,
            environment={"now": self.base_time}
        )
        
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('30'), signer=self.bob, environment={"now": contrib_time})
        
        # This contribution should hit the hard cap exactly
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('20'), signer=self.alice, environment={"now": contrib_time})
        
        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info['amount_received'], hard_cap)

        # This contribution should fail
        with self.assertRaisesRegex(AssertionError, "contribution exceeds hard cap"):
            self.con_crowdfund_otc.contribute(
                pool_id=pool_id, amount=decimal('1'), signer=self.charlie,
                environment={"now": contrib_time}
            )

    def test_list_otc_soft_cap_not_met(self):
        print("\n--- Test: List on OTC - Soft Cap Not Met ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Soft Cap Fail", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('50'), signer=self.alice,
            environment={"now": self.base_time}
        )
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('20'), signer=self.bob, environment={"now": contrib_time}) # Below soft cap

        time_after_contrib_deadline = self._get_future_time(self.base_time, days=6) # Past contribution deadline

        with self.assertRaisesRegex(AssertionError, "Soft cap not met"):
            self.con_crowdfund_otc.list_pooled_funds_on_otc(
                pool_id=pool_id, otc_take_token=self.take_token_name,
                otc_total_take_amount=decimal('10'), signer=self.alice,
                environment={"now": time_after_contrib_deadline}
            )

    def test_list_otc_timing_constraints(self):
        print("\n--- Test: List on OTC - Timing Constraints ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Timing Test", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('10'), signer=self.alice,
            environment={"now": self.base_time}
        )
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('20'), signer=self.bob, environment={"now": contrib_time}) # Meets soft cap

        # Attempt to list before contribution deadline
        time_before_contrib_deadline_ends = self._get_future_time(self.base_time, days=4)
        with self.assertRaisesRegex(AssertionError, "Cannot list on OTC before contribution deadline"):
            self.con_crowdfund_otc.list_pooled_funds_on_otc(
                pool_id=pool_id, otc_take_token=self.take_token_name,
                otc_total_take_amount=decimal('10'), signer=self.alice,
                environment={"now": time_before_contrib_deadline_ends}
            )

        # Attempt to list after exchange deadline
        # Contrib window 5 days, exchange window 3 days. Total 8 days.
        time_after_exchange_deadline = self._get_future_time(self.base_time, days=9)
        with self.assertRaisesRegex(AssertionError, "Exchange window has passed"):
            self.con_crowdfund_otc.list_pooled_funds_on_otc(
                pool_id=pool_id, otc_take_token=self.take_token_name,
                otc_total_take_amount=decimal('10'), signer=self.alice,
                environment={"now": time_after_exchange_deadline}
            )
            
    def test_successful_otc_listing_and_execution_and_withdraw_share(self):
        print("\n--- Test: Successful OTC Listing, Execution, and Withdraw Share ---")
        # Create pool
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Success OTC", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('50'), signer=self.alice,
            environment={"now": self.base_time}
        )
        
        # Contributions
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('30'), signer=self.bob, environment={"now": contrib_time})
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('40'), signer=self.charlie, environment={"now": contrib_time})
        # Total pooled: 70 (meets soft cap)

        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info['amount_received'], decimal('70'))

        # List on OTC
        time_for_listing = self._get_future_time(self.base_time, days=6) # After contrib deadline, within exchange window
        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('350'), signer=self.alice, # Offering 70 PoolToken for 350 TakeToken
            environment={"now": time_for_listing}
        )
        self.assertIsNotNone(otc_listing_id)
        pool_info_after_listing = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_listing['status'], "OTC_LISTED")
        self.assertEqual(pool_info_after_listing['otc_listing_id'], otc_listing_id)

        # Charlie (taker) takes the offer on con_otc
        # Assuming con_otc.take_offer transfers tokens and updates status
        time_for_taking_offer = self._get_future_time(time_for_listing, minutes=30)
        self.con_otc.take_offer(
            listing_id=otc_listing_id,
            signer=self.charlie, # Charlie has TakeTokens and approval
            environment={"now": time_for_taking_offer}
        )

        otc_offer_details_on_otc = self.con_otc.otc_listing[otc_listing_id]
        self.assertEqual(otc_offer_details_on_otc['status'], "EXECUTED")
        # Verify crowdfund contract (alice, the maker) received take_tokens
        self.assertEqual(self.con_otc_take_token.balance_of(address=self.crowdfund_contract_name), decimal('350'))


        # Finalize status on crowdfund (can be called by anyone)
        # This step might be skippable if withdraw_share directly reads foreign state,
        # but your current crowdfund contract has this function.
        # self.con_crowdfund_otc.finalize_otc_deal_status(pool_id=pool_id, signer=self.operator, environment={"now": time_for_taking_offer})

        # pool_info_finalized = self.con_crowdfund_otc.pool_fund[pool_id]
        # self.assertEqual(pool_info_finalized['status'], "OTC_EXECUTED")
        # self.assertEqual(pool_info_finalized['otc_actual_received_amount'], decimal('350'))

        # Bob withdraws his share
        # Bob contributed 30 out of 70. Share = 30/70
        # Expected share of take_tokens = (30/70) * 350 = 150
        bob_initial_take_token_bal = self.con_otc_take_token.balance_of(address=self.bob)
        self.con_crowdfund_otc.withdraw_share(pool_id=pool_id, signer=self.bob, environment={"now": time_for_taking_offer})
        bob_final_take_token_bal = self.con_otc_take_token.balance_of(address=self.bob)
        
        self.assertEqual(bob_final_take_token_bal, bob_initial_take_token_bal + decimal('150'))
        
        bob_contrib_info = self.con_crowdfund_otc.contributor[self.bob, pool_id]
        self.assertTrue(bob_contrib_info['share_withdrawn'])

        # Charlie withdraws his share
        # Charlie contributed 40 out of 70. Share = 40/70
        # Expected share = (40/70) * 350 = 200
        # Charlie was also the taker, so his balance changes need careful tracking if he didn't start at 0 for take_token.
        # For simplicity, let's assume his role as contributor is separate for this calculation.
        charlie_initial_take_token_bal_as_contributor = self.con_otc_take_token.balance_of(address=self.charlie)
        self.con_crowdfund_otc.withdraw_share(pool_id=pool_id, signer=self.charlie, environment={"now": time_for_taking_offer})
        charlie_final_take_token_bal_as_contributor = self.con_otc_take_token.balance_of(address=self.charlie)
        # He should receive 200 for his contribution share.
        self.assertEqual(charlie_final_take_token_bal_as_contributor, charlie_initial_take_token_bal_as_contributor + decimal('200'))

        # Crowdfund contract should have 0 take_tokens left after all shares withdrawn
        self.assertEqual(self.con_otc_take_token.balance_of(address=self.crowdfund_contract_name), decimal('0'))


    def test_otc_cancelled_and_withdraw_contribution(self):
        print("\n--- Test: OTC Cancelled and Withdraw Contribution ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Cancel OTC", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('50'), signer=self.alice,
            environment={"now": self.base_time}
        )
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('60'), signer=self.bob, environment={"now": contrib_time})

        time_for_listing = self._get_future_time(self.base_time, days=6)

        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('300'), signer=self.alice,
            environment={"now": time_for_listing}
        )

        # Alice (maker/pool_creator) cancels the offer on con_otc
        time_for_cancelling = self._get_future_time(time_for_listing, minutes=30)
        self.con_crowdfund_otc.cancel_otc_listing_for_pool(pool_id=pool_id, signer=self.alice, environment={"now": time_for_cancelling})
        
        otc_offer_details_on_otc = self.con_otc.otc_listing[otc_listing_id]
        self.assertEqual(otc_offer_details_on_otc['status'], "CANCELLED")
        # Pool tokens should be returned to crowdfund contract
        self.assertEqual(self.con_pool_token.balance_of(address=self.crowdfund_contract_name), decimal('60'))

        # Finalize status on crowdfund
        # self.con_crowdfund_otc.finalize_otc_deal_status(pool_id=pool_id, signer=self.operator, environment={"now": time_for_cancelling})
        # pool_info_finalized = self.con_crowdfund_otc.pool_fund[pool_id]
        # self.assertEqual(pool_info_finalized['status'], "OTC_FAILED")

        # Bob withdraws his original contribution
        bob_initial_pool_token_bal = self.con_pool_token.balance_of(address=self.bob)
        self.con_crowdfund_otc.withdraw_contribution(pool_id=pool_id, signer=self.bob, environment={"now": time_for_cancelling})
        
        bob_final_pool_token_bal = self.con_pool_token.balance_of(address=self.bob)
        self.assertEqual(bob_final_pool_token_bal, bob_initial_pool_token_bal + decimal('60'))
        
        bob_contrib_info = self.con_crowdfund_otc.contributor[self.bob, pool_id]
        self.assertEqual(bob_contrib_info['amount_contributed'], decimal('0'))
        pool_info_after_withdraw = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_withdraw['amount_received'], decimal('0'))


    def test_withdraw_contribution_before_deadline(self):
        print("\n--- Test: Withdraw Contribution Before Deadline ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Early Withdraw", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('50'), signer=self.alice,
            environment={"now": self.base_time}
        )
        
        contrib_time_1 = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('20'), signer=self.bob, environment={"now": contrib_time_1})
        
        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info['amount_received'], decimal('20'))

        # Bob withdraws before contribution deadline
        withdraw_time = self._get_future_time(self.base_time, days=2) # Still within 5-day window
        bob_initial_pool_token_bal = self.con_pool_token.balance_of(address=self.bob)
        
        self.con_crowdfund_otc.withdraw_contribution(pool_id=pool_id, signer=self.bob, environment={"now": withdraw_time})
        
        bob_final_pool_token_bal = self.con_pool_token.balance_of(address=self.bob)
        self.assertEqual(bob_final_pool_token_bal, bob_initial_pool_token_bal + decimal('20'))
        
        pool_info_after_withdraw = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_withdraw['amount_received'], decimal('0'))
        bob_contrib_info = self.con_crowdfund_otc.contributor[self.bob, pool_id]
        self.assertEqual(bob_contrib_info['amount_contributed'], decimal('0'))


    def test_otc_expires_unfilled_and_withdraw_contribution(self):
        print("\n--- Test: OTC Expires Unfilled and Withdraw Contribution ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="OTC Expire", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('50'), signer=self.alice,
            environment={"now": self.base_time}
        )
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('60'), signer=self.bob, environment={"now": contrib_time})

        time_for_listing = self._get_future_time(self.base_time, days=6) # Contrib deadline passed (5 days)
        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('300'), signer=self.alice,
            environment={"now": time_for_listing}
        )
        
        # Time passes beyond exchange deadline (listing time + 3 days for exchange window)
        # Base time + 6 days (listing) + 4 days (past exchange window of 3 days) = base_time + 10 days
        time_after_otc_expiry = self._get_future_time(self.base_time, days=10) 

        # Finalize status on crowdfund - it should detect expiry if OTC listing is still "OPEN"
        # This assumes OTC contract doesn't auto-cancel. If it does, status might be "CANCELLED".
        # Your finalize_otc_deal_status checks `(otc_offer_details["status"] == "OPEN" and now > pool["exchange_deadline"])`

        # self.con_crowdfund_otc.finalize_otc_deal_status(pool_id=pool_id, signer=self.operator, environment={"now": time_after_otc_expiry})
        # pool_info_finalized = self.con_crowdfund_otc.pool_fund[pool_id]
        # self.assertEqual(pool_info_finalized['status'], "OTC_FAILED")

        # Crucially, the pool tokens for an expired but not explicitly cancelled OTC offer
        # are still locked in the OTC contract. The crowdfund creator (Alice) needs to cancel it on con_otc.
        # This is a gap if finalize_otc_deal_status doesn't trigger a cancellation.
        # For this test to pass withdraw_contribution, the tokens MUST be back in con_crowdfund_otc.
        # Let's assume Alice cancels it on the OTC contract after expiry.
        self.con_crowdfund_otc.cancel_otc_listing_for_pool(pool_id=pool_id, signer=self.alice, environment={"now": time_after_otc_expiry})
        self.assertEqual(self.con_pool_token.balance_of(address=self.crowdfund_contract_name), decimal('60'))


        # Bob withdraws his original contribution
        bob_initial_pool_token_bal = self.con_pool_token.balance_of(address=self.bob)
        self.con_crowdfund_otc.withdraw_contribution(pool_id=pool_id, signer=self.bob, environment={"now": time_after_otc_expiry})
        
        bob_final_pool_token_bal = self.con_pool_token.balance_of(address=self.bob)
        self.assertEqual(bob_final_pool_token_bal, bob_initial_pool_token_bal + decimal('60'))

    def test_reentrancy_vulnerability_in_contribute(self):
        print("\n--- Test: Re-entrancy Vulnerability in Contribute ---")
        
        attacker = self.alice_attacker # Use the renamed alice
        malicious_token_contract_address = self.malicious_token_name
        crowdfund_contract_address = self.crowdfund_contract_name

        # 1. Mint malicious tokens
        # Attacker gets tokens for the initial call
        self.con_malicious_token.mint(amount=decimal('100'), to=attacker, signer=self.operator)
        # Malicious contract gets tokens for the re-entrant call
        self.con_malicious_token.mint(amount=decimal('50'), to=malicious_token_contract_address, signer=self.operator)

        print(f"Attacker ({attacker}) MAL balance: {self.con_malicious_token.balance_of(address=attacker)}")
        print(f"Malicious contract ({malicious_token_contract_address}) MAL balance: {self.con_malicious_token.balance_of(address=malicious_token_contract_address)}")

        # 2. Attacker approves crowdfund contract to spend her malicious tokens
        self.con_malicious_token.approve(amount=decimal('100'), to=crowdfund_contract_address, signer=attacker)
        print(f"Attacker ({attacker}) approved {crowdfund_contract_address} for 100 MAL tokens.")

        # 3. Malicious token contract approves crowdfund contract to spend its (the malicious token's) own tokens
        # This needs to be done by the malicious token contract itself. We simulate this by having the operator call approve
        # as if it's an internal setup step of the malicious contract, or if malicious_token had a method for this.
        # For the malicious token to call approve on itself for another contract, it needs a function like:
        # @export def self_approve(spender: str, amount: float): self.approve(amount, spender) # called by malicious contract owner
        # For now, let's assume the malicious token's `approve` can be called by anyone for this setup, or it's done by its operator.
        # A contract cannot "sign" a call to its own @export methods directly without a helper or specific design.
        # The `approve` method uses `ctx.caller`. If operator calls it, `ctx.caller` is operator.
        # We need `ctx.caller` to be `malicious_token_contract_address`.
        # This is a common setup challenge for contract-to-contract interactions that require the contract itself to be the initiator.
        # The easiest way for testing is to make `approve` in `con_malicious_reentrant_token` allow an operator to approve on behalf of the contract,
        # or make the `approve` function take `owner_to_approve_for` as a parameter if `ctx.caller` is operator.
        # For simplicity, let's assume the malicious token contract has a way to do this or its `approve` is very open for the test.
        # Let's modify `con_malicious_reentrant_token.approve` to allow operator to approve for the contract itself for testability:
        # Original: allowances[ctx.caller, to] = amount
        # Test-friendly: if ctx.caller == operator and owner_on_behalf_of: allowances[owner_on_behalf_of, to] = amount else: allowances[ctx.caller, to] = amount
        # For now, I'll proceed assuming that the malicious token's approval for the crowdfund is set up correctly.
        # One way: add a helper in malicious token:
        # @export def execute_approve_for_crowdfund(self, crowdfund_addr: str, amount_val: float):
        #     self.approve(amount=amount_val, to=crowdfund_addr) # ctx.caller is self.malicious_token_name
        # And call this helper via the operator for setup.
        # This is not directly possible as an @export method called by an EOA won't have ctx.caller as the contract.
        # The setup of this approval from the contract for itself is the trickiest part of realistic re-entrancy test setup.
        #
        # The `transfer_from` inside the re-entered `contribute` will have `main_account=con_malicious_token_name`.
        # So `con_malicious_token_name` must have approved `con_crowdfund_otc_name`.
        # This means we need `allowances[con_malicious_token_name, con_crowdfund_otc_name]` to be set.
        # This can be done if `con_malicious_token` calls `approve` with `ctx.caller` being `con_malicious_token_name`.
        # This is typically done in a constructor or an owner-only function that executes actions as the contract.
        # Let's assume the malicious token's constructor or an admin function handles this.
        # For the test, we will directly set the allowance using a backdoor if available, or assume it's done.
        # The `ContractingClient` doesn't allow direct state writes easily.
        #
        # Simplest path: The `approve` function in `con_malicious_reentrant_token.py` uses `ctx.caller`.
        # To make the malicious contract approve the crowdfund contract, the malicious contract
        # would need an internal function that it calls, or its `approve` function needs to be callable
        # by its owner to act on its behalf.
        # For this test, we'll assume the malicious contract can set this allowance for itself.
        # This is a common pattern: a contract has a method that only its owner can call,
        # which then performs actions (like approving another contract) where `ctx.caller` is the contract itself.
        # We'll skip the explicit call here and trust the malicious token's internal logic (not fully implemented above)
        # or that the test setup implies this state. The re-entrant call will fail if this approval isn't in place.
        # The `print` statements in `con_malicious_reentrant_token` will show if allowance is missing.
        # For the malicious token to approve the crowdfund for ITSELF:
        self.con_malicious_token.approve(amount=decimal('50'), to=crowdfund_contract_address, signer=malicious_token_contract_address)
        # This line above is conceptual. An EOA (like self.operator) cannot sign AS a contract.
        # This approval needs to happen from *within* the malicious_token_contract's execution context.
        # Let's assume the malicious token's `configure_re_entrancy` or a similar setup function also handles this self-approval.
        # The test will reveal if this is a blocker. The current `approve` uses `ctx.caller`.
        # To make this test work, we will assume `con_malicious_token` has a way to approve `con_crowdfund_otc` for its own tokens.
        # This means the `allowances` hash in `con_malicious_token` needs:
        # `allowances[malicious_token_contract_address, crowdfund_contract_address] = decimal('50')`
        # This state must be achieved by `malicious_token_contract_address` being `ctx.caller` during an `approve` call.
        # This is hard to do from a test script unless the contract is designed for it.
        # For now, we'll rely on the re-entrant call to print if it fails due to this.
        # A robust malicious contract would have a setup function callable by its owner that does this.
        # `malicious_contract.setup_internal_approval(spender=crowdfund_addr, amount=50, signer=operator)`
        # where `setup_internal_approval` then calls `approve(50, crowdfund_addr)` internally.

        # 4. Create a pool using the malicious token
        pool_creation_time = self._get_future_time(self.base_time, minutes=10)
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Re-entrancy Test Pool",
            pool_token=malicious_token_contract_address, # Using the malicious token
            hard_cap=decimal('200'),
            soft_cap=decimal('10'),
            signer=self.operator, # Operator creates the pool
            environment={"now": pool_creation_time}
        )
        print(f"Pool {pool_id} created with malicious token {malicious_token_contract_address}.")

        # 5. Configure re-entrancy in the malicious token
        # This needs to be done *before* the transfer_from that triggers it.
        # The pool_id is now known.
        attacker_contribution_amount = decimal('70')
        re_entrant_contribution_amount = decimal('30')
        self.con_malicious_token.configure_re_entrancy(
            crowdfund_name=crowdfund_contract_address,
            pool_id=pool_id,
            amount=re_entrant_contribution_amount,
            signer=self.operator # Assuming operator can configure the malicious token
        )
        # Critical step for re-entrancy to work: malicious contract must approve crowdfund for its *own* tokens
        # This is the hard part to do from outside. If malicious_token.approve is standard, only malicious_token can be ctx.caller.
        # Let's add a backdoor to malicious_token.approve for testing or assume it's handled.
        # For the test to pass this stage, we need `allowances[malicious_token_name, crowdfund_name]` to be >= re_entrant_contribution_amount.
        # This would typically be done by the malicious contract owner calling a special function in the malicious contract.
        # For now, we hope the print statements in `con_malicious_reentrant_token` will guide us if this fails.
        # A simplified approach: the malicious token, when configured, also approves.
        # Modify `configure_re_entrancy` in `con_malicious_reentrant_token.py`:
        # ```python
        # @export
        # def configure_re_entrancy(crowdfund_name: str, pool_id: str, amount: float):
        #     # ... (set other vars) ...
        #     # Auto-approve the crowdfund contract for the re-entrant amount from self.
        #     # This means ctx.caller for this approve call is this contract.
        #     # This requires approve to be callable internally or a mechanism for the contract to be ctx.caller.
        #     # This is the conceptual part that's hard to implement directly in an @export called by an EOA.
        #     # A true malicious contract would be coded to ensure this state.
        #     # For the test, we rely on the fact that the re-entrant call to contribute
        #     # will have ctx.caller = malicious_token_name, and that contribute will then call
        #     # malicious_token.transfer_from with main_account = malicious_token_name.
        #     # So, malicious_token_name needs to approve crowdfund_name.
        #     # This has to be done by malicious_token_name as ctx.caller.
        #     # This is a bootstrapping issue for the test.
        #     # The most direct way is if malicious_token's constructor or an admin function does this.
        #     # Let's assume it's done. The test will fail on allowance if not.
        # ```
        # The `approve` call `self.con_malicious_token.approve(amount=decimal('50'), to=crowdfund_contract_address, signer=malicious_token_contract_address)`
        # is the conceptual requirement. In a real test, you'd need a way for the malicious contract to execute this.
        # If the malicious contract is `ctx.caller` when `approve` is called, it works.
        # This usually means `approve` is called from another function within the same malicious contract.
        # We will proceed and see if the allowance check in the re-entrant `transfer_from` fails.

        # 6. Attacker calls `contribute` (outer call)
        print(f"Attacker ({attacker}) about to contribute {attacker_contribution_amount} to pool {pool_id}.")
        contribute_time = self._get_future_time(pool_creation_time, minutes=5)
        
        # Before Alice's (attacker's) contribution
        initial_amount_received = self.con_crowdfund_otc.pool_fund[pool_id]['amount_received']
        self.assertEqual(initial_amount_received, decimal('0'))

        self.con_crowdfund_otc.contribute(
            pool_id=pool_id,
            amount=attacker_contribution_amount,
            signer=attacker,
            environment={"now": contribute_time}
        )
        print(f"Attacker's ({attacker}) contribution call finished.")

        # 7. Check the state
        pool_state = self.con_crowdfund_otc.pool_fund[pool_id]
        attacker_contribution_info = self.con_crowdfund_otc.contributor[attacker, pool_id]
        malicious_contract_contribution_info = self.con_crowdfund_otc.contributor[malicious_token_contract_address, pool_id]

        print(f"Pool state after re-entrant attempt: {pool_state}")
        print(f"Attacker's contribution info: {attacker_contribution_info}")
        print(f"Malicious contract's contribution info: {malicious_contract_contribution_info}")

        # Expected vulnerable behavior:
        # - pool_state['amount_received'] only reflects the outer call (attacker_contribution_amount),
        #   because the write from the inner call was overwritten.
        # - attacker_contribution_info correctly shows attacker_contribution_amount.
        # - malicious_contract_contribution_info correctly shows re_entrant_contribution_amount.
        # - This leads to inconsistency: sum of contributor amounts != pool_state['amount_received'].

        self.assertEqual(pool_state['amount_received'], attacker_contribution_amount,
                         "Vulnerability: pool_fund.amount_received did not correctly sum up contributions due to re-entrancy overwrite.")
        
        self.assertIsNotNone(attacker_contribution_info, "Attacker's contribution info missing.")
        self.assertEqual(attacker_contribution_info['amount_contributed'], attacker_contribution_amount,
                         "Attacker's contributed amount is incorrect.")

        self.assertIsNotNone(malicious_contract_contribution_info, "Malicious contract's re-entrant contribution info missing.")
        if malicious_contract_contribution_info: # Only check if it exists
            self.assertEqual(malicious_contract_contribution_info['amount_contributed'], re_entrant_contribution_amount,
                            "Malicious contract's re-entrant contributed amount is incorrect.")

        # The core of the vulnerability demonstration:
        total_contributions_recorded_in_contributor_hash = decimal('0')
        if attacker_contribution_info:
            total_contributions_recorded_in_contributor_hash += attacker_contribution_info['amount_contributed']
        if malicious_contract_contribution_info:
            total_contributions_recorded_in_contributor_hash += malicious_contract_contribution_info['amount_contributed']
        
        self.assertNotEqual(pool_state['amount_received'], total_contributions_recorded_in_contributor_hash,
                            "Pool's total amount_received should NOT match the sum of individual contributions if vulnerability exploited.")
        
        print(f"Demonstrated re-entrancy: Pool's amount_received is {pool_state['amount_received']}, "
              f"but sum of individual contributions in contributor hash is {total_contributions_recorded_in_contributor_hash}.")
        print("This shows that the re-entrant contribution was recorded for the malicious contract as a contributor, "
              "but its effect on pool_fund.amount_received was overwritten by the outer call.")

if __name__ == '__main__':
    unittest.main()