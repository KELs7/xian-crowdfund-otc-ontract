import unittest
from contracting.stdlib.bridge.decimal import ContractingDecimal as decimal
from contracting.stdlib.bridge.time import Datetime, Timedelta 
from contracting.client import ContractingClient
from pathlib import Path

class TestCrowdfundContractMoreCases(unittest.TestCase):
    def setUp(self):
        self.client = ContractingClient()
        self.client.flush() 

        self.operator = 'sys' 
        self.alice = 'alice' # Pool creator
        self.bob = 'bob'     # Contributor
        self.charlie = 'charlie' # Contributor / OTC Taker
        self.dave = 'dave'   # Another user

        self.crowdfund_contract_name = "con_crowdfund_otc"
        self.otc_contract_name = "con_otc"
        self.pool_token_name = "con_pool_token"
        self.take_token_name = "con_otc_take_token"
        self.malicious_token_name = "con_malicious_reentrant_token" # For completeness if needed

        current_dir = Path(__file__).resolve().parent.parent

        with open(current_dir / "con_crowdfund_otc.py") as f:
            self.client.submit(f.read(), name=self.crowdfund_contract_name, signer=self.operator)
        with open(current_dir / "con_otc.py") as f:
            self.client.submit(f.read(), name=self.otc_contract_name, signer=self.operator)
        with open(current_dir / "con_pool_token.py") as f:
            self.client.submit(f.read(), name=self.pool_token_name, signer=self.operator)
        with open(current_dir / "con_otc_take_token.py") as f:
            self.client.submit(f.read(), name=self.take_token_name, signer=self.operator)
        # Malicious token not strictly needed for these new tests but good to have in setup
        with open(current_dir / "con_malicious_reentrant_token.py") as f:
            self.client.submit(f.read(), name=self.malicious_token_name, signer=self.operator)


        self.con_crowdfund_otc = self.client.get_contract(self.crowdfund_contract_name)
        self.con_otc = self.client.get_contract(self.otc_contract_name)
        self.con_pool_token = self.client.get_contract(self.pool_token_name)
        self.con_otc_take_token = self.client.get_contract(self.take_token_name)

        # Token Distribution
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.alice, signer=self.operator)
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.bob, signer=self.operator)
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.charlie, signer=self.operator)
        self.con_otc_take_token.transfer(amount=decimal('5000'), to=self.charlie, signer=self.operator)
        self.con_otc_take_token.transfer(amount=decimal('5000'), to=self.dave, signer=self.operator)


        # Approvals for crowdfund contributions
        self.con_pool_token.approve(amount=decimal('1000'), to=self.crowdfund_contract_name, signer=self.alice)
        self.con_pool_token.approve(amount=decimal('1000'), to=self.crowdfund_contract_name, signer=self.bob)
        self.con_pool_token.approve(amount=decimal('1000'), to=self.crowdfund_contract_name, signer=self.charlie)

        # Approval for OTC take (Charlie and Dave might take offers)
        self.con_otc_take_token.approve(amount=decimal('5000'), to=self.otc_contract_name, signer=self.charlie)
        self.con_otc_take_token.approve(amount=decimal('5000'), to=self.otc_contract_name, signer=self.dave)


        self.base_time = Datetime(year=2024, month=1, day=1, hour=0, minute=0, second=0)
        
        # Set OTC contract in crowdfund metadata
        self.con_crowdfund_otc.change_metadata(key='otc_contract', value=self.otc_contract_name, signer=self.operator)


    def tearDown(self):
        self.client.flush()

    def _get_future_time(self, base_dt: Datetime, days=0, hours=0, minutes=0, seconds=0) -> Datetime:
        delta = Timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return base_dt + delta

    def test_change_metadata_permissions_and_effects(self):
        print("\n--- Test: Change Metadata Permissions and Effects ---")
        # Operator changes contribution_window
        new_contrib_window_days = 7
        new_contrib_window = Timedelta(days=new_contrib_window_days)
        self.con_crowdfund_otc.change_metadata(
            key='contribution_window', value=new_contrib_window, signer=self.operator
        )
        self.assertEqual(self.con_crowdfund_otc.metadata['contribution_window'], new_contrib_window)

        # Non-operator (Alice) fails to change metadata
        with self.assertRaisesRegex(AssertionError, "Only operator can set metadata"):
            self.con_crowdfund_otc.change_metadata(
                key='exchange_window', value=Timedelta(days=1), signer=self.alice
            )

        # Create a new pool and verify it uses the updated contribution_window
        pool_creation_time = self._get_future_time(self.base_time, hours=1)
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Pool with new window", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('10'), signer=self.alice,
            environment={"now": pool_creation_time}
        )
        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        expected_deadline = pool_creation_time + new_contrib_window
        self.assertEqual(pool_info['contribution_deadline'], expected_deadline)

    def test_create_pool_invalid_inputs(self):
        print("\n--- Test: Create Pool Invalid Inputs ---")
        long_description = "a" * (self.con_crowdfund_otc.metadata['description_length'] + 1)
        with self.assertRaisesRegex(AssertionError, "description too long"):
            self.con_crowdfund_otc.create_pool(
                description=long_description, pool_token=self.pool_token_name,
                hard_cap=decimal('100'), soft_cap=decimal('50'), signer=self.alice
            )

        with self.assertRaisesRegex(AssertionError, "hard cap amount should be greater than soft cap amount"):
            self.con_crowdfund_otc.create_pool(
                description="Invalid caps", pool_token=self.pool_token_name,
                hard_cap=decimal('50'), soft_cap=decimal('100'), signer=self.alice
            )
        
        with self.assertRaisesRegex(AssertionError, "hard cap amount should be greater than soft cap amount"):
            self.con_crowdfund_otc.create_pool(
                description="Invalid caps", pool_token=self.pool_token_name,
                hard_cap=decimal('50'), soft_cap=decimal('50'), signer=self.alice
            )

        with self.assertRaisesRegex(AssertionError, "soft cap must be positive"):
            self.con_crowdfund_otc.create_pool(
                description="Invalid soft cap", pool_token=self.pool_token_name,
                hard_cap=decimal('100'), soft_cap=decimal('0'), signer=self.alice
            )

    def test_contribute_failures(self):
        print("\n--- Test: Contribute Failures ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Contrib Failures Pool", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('10'), signer=self.alice,
            environment={"now": self.base_time}
        )
        
        contrib_time = self._get_future_time(self.base_time, days=1)

        with self.assertRaisesRegex(AssertionError, "pool does not exist"):
            self.con_crowdfund_otc.contribute(pool_id="non_existent_pool", amount=decimal('5'), signer=self.bob, environment={"now": contrib_time})

        with self.assertRaisesRegex(AssertionError, "contribution amount must be positive"):
            self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('0'), signer=self.bob, environment={"now": contrib_time})

        # Test insufficient allowance (Bob has 1000 allowance, try to contribute 2000)
        # First, reduce Bob's allowance to test this specifically
        self.con_pool_token.approve(amount=decimal('5'), to=self.crowdfund_contract_name, signer=self.bob)
        with self.assertRaisesRegex(AssertionError, "Transfer amount exceeds allowance"): # This error comes from the token contract
            self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('10'), signer=self.bob, environment={"now": contrib_time})
        # Restore allowance for other tests
        self.con_pool_token.approve(amount=decimal('1000'), to=self.crowdfund_contract_name, signer=self.bob)


        # Test insufficient balance (Dave has 0 pool_tokens)
        # Dave has Pool Token allowance but 0 balance
        self.con_pool_token.approve(amount=decimal('100'), to=self.crowdfund_contract_name, signer=self.dave)
        self.assertEqual(self.con_pool_token.balance_of(address=self.dave), decimal('0'))
        with self.assertRaisesRegex(AssertionError, "Transfer amount exceeds balance"): # This error comes from the token contract
            self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('10'), signer=self.dave, environment={"now": contrib_time})

        # Change pool status and try to contribute
        self.con_crowdfund_otc.change_metadata(key='operator', value=self.alice, signer=self.operator) # Allow Alice to change pool state for test
        # This is a hacky way to change status for testing. Ideally, status changes via defined flows.
        # For this test, we'll assume a hypothetical direct state change capability for the operator.
        # A better way would be to drive the pool to a different state (e.g., "REFUNDING") legitimately.
        # For now, we'll test by trying to contribute after contribution window closes.
        time_after_deadline = self._get_future_time(self.base_time, days=6) # Default window is 5 days
        with self.assertRaisesRegex(AssertionError, "contribution window closed"):
            self.con_crowdfund_otc.contribute(
                pool_id=pool_id, amount=decimal('5'), signer=self.bob, environment={"now": time_after_deadline}
            )
        self.con_crowdfund_otc.change_metadata(key='operator', value=self.operator, signer=self.alice) # Revert operator

    def test_list_otc_permissions_and_invalid_states(self):
        print("\n--- Test: List on OTC Permissions and Invalid States ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="List OTC States", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('10'), signer=self.alice, # Alice is creator
            environment={"now": self.base_time}
        )
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('20'), signer=self.bob, environment={"now": contrib_time}) # Soft cap met

        time_for_listing = self._get_future_time(self.base_time, days=6) # Valid time to list

        # Attempt by non-pool_creator (Bob)
        with self.assertRaisesRegex(AssertionError, "Only pool creator can initiate OTC listing"):
            self.con_crowdfund_otc.list_pooled_funds_on_otc(
                pool_id=pool_id, otc_take_token=self.take_token_name,
                otc_total_take_amount=decimal('100'), signer=self.bob,
                environment={"now": time_for_listing}
            )

        # Invalid otc_total_take_amount
        with self.assertRaisesRegex(AssertionError, "OTC take amount must be positive"):
            self.con_crowdfund_otc.list_pooled_funds_on_otc(
                pool_id=pool_id, otc_take_token=self.take_token_name,
                otc_total_take_amount=decimal('0'), signer=self.alice,
                environment={"now": time_for_listing}
            )
        
        # Successfully list once
        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('100'), signer=self.alice,
            environment={"now": time_for_listing}
        )
        self.assertIsNotNone(otc_listing_id)
        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info['status'], "OTC_LISTED")

        # Attempt to list again when already listed
        with self.assertRaisesRegex(AssertionError, "OTC deal already initiated for this pool."):
            self.con_crowdfund_otc.list_pooled_funds_on_otc(
                pool_id=pool_id, otc_take_token=self.take_token_name,
                otc_total_take_amount=decimal('100'), signer=self.alice,
                environment={"now": self._get_future_time(time_for_listing, seconds=1)}
            )
        
        # Test OTC fee calculation (example)
        # Set OTC fee to 1%
        self.con_otc.adjust_fee(trading_fee=decimal('1.0'), signer=self.operator) # OTC owner is operator
        
        pool_id2 = self.con_crowdfund_otc.create_pool(
            description="List OTC Fee Test", pool_token=self.pool_token_name,
            hard_cap=decimal('200'), soft_cap=decimal('80'), signer=self.bob, # Bob is creator
            environment={"now": self._get_future_time(self.base_time, days=7)}
        )
        self.con_crowdfund_otc.contribute(pool_id=pool_id2, amount=decimal('100'), signer=self.alice, environment={"now": self._get_future_time(self.base_time, days=7)})
        
        # Bob lists
        otc_listing_id2 = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id2, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('500'), signer=self.bob, # Bob is creator
            environment={"now": self._get_future_time(time_for_listing, days=7)}
        )
        otc_offer_on_otc_contract = self.con_otc.otc_listing[otc_listing_id2]
        # pooled_amount = decimal('100')
        # otc_fee_percent = decimal('1.0')
        # fee_rate = decimal('1.0') / decimal('100.0') = decimal('0.01')
        # expected_offer_amount = decimal('100') / (decimal('1.0') + decimal('0.01'))
        # expected_offer_amount = decimal('100') / decimal('1.01')
        self.assertEqual(otc_offer_on_otc_contract['offer_amount'], decimal('100') / decimal('1.01'))


    def test_cancel_otc_listing_permissions_and_states_by_operator(self):
        print("\n--- Test: Cancel OTC Listing by Operator ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Cancel by Op", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('10'), signer=self.alice, # Alice is creator
            environment={"now": self.base_time}
        )
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('20'), signer=self.bob, environment={"now": contrib_time})

        time_for_listing = self._get_future_time(self.base_time, days=6)
        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('100'), signer=self.alice,
            environment={"now": time_for_listing}
        )
        
        # Operator cancels the listing
        time_for_cancelling = self._get_future_time(time_for_listing, minutes=10)
        self.con_crowdfund_otc.cancel_otc_listing_for_pool(
            pool_id=pool_id, signer=self.operator, environment={"now": time_for_cancelling}
        )
        
        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info['status'], "OTC_FAILED")
        otc_offer_on_otc = self.con_otc.otc_listing[otc_listing_id]
        self.assertEqual(otc_offer_on_otc['status'], "CANCELLED")
        # Check funds returned to crowdfund contract
        self.assertEqual(self.con_pool_token.balance_of(address=self.crowdfund_contract_name), decimal('20'))


    def test_withdraw_contribution_if_creator_never_lists_on_otc(self):
        print("\n--- Test: Withdraw Contrib if Creator Fails to List on OTC ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Creator No Action", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('10'), signer=self.alice, # Alice is creator
            environment={"now": self.base_time}
        )
        
        contrib_time = self._get_future_time(self.base_time, days=1)
        contribution_amount = decimal('25')
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=contribution_amount, signer=self.bob, environment={"now": contrib_time})
        
        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info['amount_received'], contribution_amount) # Soft cap met

        # Time passes beyond contribution AND exchange deadlines. Alice (creator) does nothing.
        # Contribution window: 5 days. Exchange window: 3 days. Total: 8 days.
        time_after_all_deadlines = self._get_future_time(self.base_time, days=9)

        # Bob should be able to withdraw his contribution
        bob_initial_balance = self.con_pool_token.balance_of(address=self.bob)
        self.con_crowdfund_otc.withdraw_contribution(
            pool_id=pool_id, signer=self.bob, environment={"now": time_after_all_deadlines}
        )
        
        bob_final_balance = self.con_pool_token.balance_of(address=self.bob)
        self.assertEqual(bob_final_balance, bob_initial_balance + contribution_amount)
        
        pool_info_after_withdraw = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_withdraw['amount_received'], decimal('0'))
        # The status should reflect failure
        self.assertEqual(pool_info_after_withdraw['status'], "OTC_FAILED") # or "REFUNDING"
        
        bob_contrib_info = self.con_crowdfund_otc.contributor[self.bob, pool_id]
        self.assertEqual(bob_contrib_info['amount_contributed'], decimal('0'))


    def test_withdraw_contribution_soft_cap_not_met_after_deadlines(self):
        print("\n--- Test: Withdraw Contrib if Soft Cap Not Met and Deadlines Pass ---")
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Soft Cap Fail, Time Up", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('50'), signer=self.alice,
            environment={"now": self.base_time}
        )
        
        contrib_time = self._get_future_time(self.base_time, days=1)
        contribution_amount = decimal('25') # Less than soft cap
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=contribution_amount, signer=self.bob, environment={"now": contrib_time})
        
        pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertTrue(pool_info['amount_received'] < pool_info['soft_cap'])

        time_after_all_deadlines = self._get_future_time(self.base_time, days=9)

        bob_initial_balance = self.con_pool_token.balance_of(address=self.bob)
        self.con_crowdfund_otc.withdraw_contribution(
            pool_id=pool_id, signer=self.bob, environment={"now": time_after_all_deadlines}
        )
        
        bob_final_balance = self.con_pool_token.balance_of(address=self.bob)
        self.assertEqual(bob_final_balance, bob_initial_balance + contribution_amount)
        
        pool_info_after_withdraw = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_withdraw['amount_received'], decimal('0'))
        self.assertEqual(pool_info_after_withdraw['status'], "OTC_FAILED") # Or "REFUNDING"

    def test_withdraw_share_failures(self):
        print("\n--- Test: Withdraw Share Failures ---")
        # Setup for a successful OTC execution first
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Withdraw Share Fail", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('50'), signer=self.alice,
            environment={"now": self.base_time}
        )
        contrib_time = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('30'), signer=self.bob, environment={"now": contrib_time})
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=decimal('40'), signer=self.charlie, environment={"now": contrib_time})

        time_for_listing = self._get_future_time(self.base_time, days=6)
        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('350'), signer=self.alice,
            environment={"now": time_for_listing}
        )
        
        time_for_taking_offer = self._get_future_time(time_for_listing, minutes=30)
        self.con_otc.take_offer(
            listing_id=otc_listing_id, signer=self.dave, # Dave takes the offer
            environment={"now": time_for_taking_offer}
        )
        
        # Bob withdraws his share successfully
        self.con_crowdfund_otc.withdraw_share(pool_id=pool_id, signer=self.bob, environment={"now": time_for_taking_offer})
        
        # Bob tries to withdraw share again
        with self.assertRaisesRegex(AssertionError, "share already withdrawn"):
            self.con_crowdfund_otc.withdraw_share(pool_id=pool_id, signer=self.bob, environment={"now": time_for_taking_offer})

        # Dave (not a contributor) tries to withdraw share
        with self.assertRaisesRegex(AssertionError, "no original contribution to claim a share for"):
            self.con_crowdfund_otc.withdraw_share(pool_id=pool_id, signer=self.dave, environment={"now": time_for_taking_offer})

        # Test case where OTC deal was cancelled, then try to withdraw share
        pool_id_cancel = self.con_crowdfund_otc.create_pool(
            description="Cancel then Share Fail", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('10'), signer=self.alice,
            environment={"now": self._get_future_time(time_for_listing, days=1)}
        )
        self.con_crowdfund_otc.contribute(pool_id=pool_id_cancel, amount=decimal('20'), signer=self.bob, environment={"now": self._get_future_time(contrib_time, days=6)})
        self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id_cancel, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('100'), signer=self.alice,
            environment={"now": self._get_future_time(time_for_listing, days=7)}
        )
        self.con_crowdfund_otc.cancel_otc_listing_for_pool(pool_id=pool_id_cancel, signer=self.alice, environment={"now": self._get_future_time(time_for_taking_offer, days=7)})
        
        with self.assertRaisesRegex(AssertionError, "OTC deal not successfully executed"):
            self.con_crowdfund_otc.withdraw_share(pool_id=pool_id_cancel, signer=self.bob, environment={"now": self._get_future_time(time_for_taking_offer, days=7)})

    def test_view_functions_various_stages(self):
        print("\n--- Test: View Functions at Various Stages ---")
        # Initial state
        pool_id = self.con_crowdfund_otc.create_pool(
            description="View Test", pool_token=self.pool_token_name,
            hard_cap=decimal('100'), soft_cap=decimal('10'), signer=self.alice,
            environment={"now": self.base_time}
        )
        pool_info = self.con_crowdfund_otc.get_pool_info(pool_id=pool_id)
        self.assertEqual(pool_info['pool_creator'], self.alice)
        self.assertEqual(pool_info['status'], "OPEN_FOR_CONTRIBUTION")
        
        contrib_info_bob_initial = self.con_crowdfund_otc.get_contribution_info(pool_id=pool_id, account=self.bob)
        self.assertIsNone(contrib_info_bob_initial) # Bob hasn't contributed yet

        otc_deal_info_initial = self.con_crowdfund_otc.get_otc_deal_info_for_pool(pool_id=pool_id)
        self.assertIsNone(otc_deal_info_initial)

        # After contribution
        contrib_time = self._get_future_time(self.base_time, days=1)
        bob_contrib_amount = decimal('20')
        self.con_crowdfund_otc.contribute(pool_id=pool_id, amount=bob_contrib_amount, signer=self.bob, environment={"now": contrib_time})
        
        contrib_info_bob_after = self.con_crowdfund_otc.get_contribution_info(pool_id=pool_id, account=self.bob)
        self.assertEqual(contrib_info_bob_after['amount_contributed'], bob_contrib_amount)

        # After listing on OTC
        time_for_listing = self._get_future_time(self.base_time, days=6)
        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id, otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('100'), signer=self.alice,
            environment={"now": time_for_listing}
        )
        pool_info_listed = self.con_crowdfund_otc.get_pool_info(pool_id=pool_id)
        self.assertEqual(pool_info_listed['status'], "OTC_LISTED")
        self.assertEqual(pool_info_listed['otc_listing_id'], otc_listing_id)

        otc_deal_info_listed = self.con_crowdfund_otc.get_otc_deal_info_for_pool(pool_id=pool_id)
        self.assertEqual(otc_deal_info_listed['listing_id'], otc_listing_id)
        self.assertEqual(otc_deal_info_listed['listed_pool_token_amount'], bob_contrib_amount)
    
    # --- This test case was used to demonstrate that there was the vulnerability of trapped pool tokens ---
    # def test_vulnerability_trapped_tokens_due_to_fee_miscalculation_on_listing(self):
    #     print("\n--- Test: Vulnerability - Trapped Pool Tokens Due to Fee Miscalculation on OTC Listing ---")
        
    #     # Scenario:
    #     # 1. OTC contract has a non-zero maker fee (F%).
    #     # 2. Crowdfund (CF) contract pre-calculates Fee_A = P * F/100 on total pool_amount (P).
    #     #    CF passes OfferAmount_Arg = P - Fee_A to OTC.list_offer.
    #     # 3. OTC contract calculates its Fee_B = OfferAmount_Arg * F/100.
    #     #    OTC pulls OfferAmount_Arg + Fee_B from CF.
    #     # 4. Total tokens pulled from CF = P(1 - F/100)(1 + F/100) = P * (1 - (F/100)^2).
    #     # 5. CF approved P tokens for OTC to spend.
    #     # 6. Tokens remaining trapped in CF = P - P(1 - (F/100)^2) = P * (F/100)^2.

    #     otc_fee_percentage = decimal('10.0') # 10% fee for pronounced effect
    #     self.con_otc.adjust_fee(trading_fee=otc_fee_percentage, signer=self.operator)
        
    #     # Record initial balance of pool tokens in the crowdfund contract.
    #     # This helps isolate changes for this specific test.
    #     initial_cf_pool_token_balance = self.con_pool_token.balance_of(address=self.crowdfund_contract_name)

    #     # Alice creates a pool
    #     pool_id = self.con_crowdfund_otc.create_pool(
    #         description="Trapped Token Test Pool", 
    #         pool_token=self.pool_token_name,
    #         hard_cap=decimal('200'), 
    #         soft_cap=decimal('50'), 
    #         signer=self.alice,
    #         environment={"now": self.base_time}
    #     )
        
    #     # Bob contributes P tokens
    #     contribution_amount_p = decimal('100.0') # Use decimal for precision in calculations
    #     contrib_time = self._get_future_time(self.base_time, days=1)
    #     # Ensure Bob has enough allowance (setUp provides 1000)
    #     self.con_crowdfund_otc.contribute(
    #         pool_id=pool_id, 
    #         amount=contribution_amount_p, 
    #         signer=self.bob, 
    #         environment={"now": contrib_time}
    #     )
        
    #     # Crowdfund contract's balance of pool_token should now be initial_cf_pool_token_balance + P
    #     expected_balance_after_contrib = initial_cf_pool_token_balance + contribution_amount_p
    #     self.assertEqual(
    #         self.con_pool_token.balance_of(address=self.crowdfund_contract_name),
    #         expected_balance_after_contrib,
    #         "Crowdfund contract balance mismatch after contribution."
    #     )
        
    #     pool_info = self.con_crowdfund_otc.pool_fund[pool_id]
    #     self.assertEqual(pool_info['amount_received'], contribution_amount_p)

    #     # Alice lists the pool on OTC
    #     time_for_listing = self._get_future_time(self.base_time, days=6) # After contrib deadline
    #     otc_total_take_amount = decimal('500.0') # Arbitrary take amount
        
    #     otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
    #         pool_id=pool_id, 
    #         otc_take_token=self.take_token_name,
    #         otc_total_take_amount=otc_total_take_amount, 
    #         signer=self.alice,
    #         environment={"now": time_for_listing}
    #     )
    #     self.assertIsNotNone(otc_listing_id, "OTC listing failed.")

    #     # Calculate expected trapped tokens: P * (F_rate)^2
    #     fee_rate = otc_fee_percentage / decimal('100.0')
    #     expected_trapped_tokens = contribution_amount_p * (fee_rate * fee_rate)
        
    #     # Expected balance in crowdfund contract after listing = initial_balance_before_this_pool_ops + P*F_rate^2
    #     expected_balance_after_listing = initial_cf_pool_token_balance + expected_trapped_tokens
    #     current_cf_balance_after_listing = self.con_pool_token.balance_of(address=self.crowdfund_contract_name)
        
    #     self.assertEqual(
    #         current_cf_balance_after_listing,
    #         expected_balance_after_listing,
    #         f"Crowdfund contract balance mismatch after OTC listing. Expected trapped: {expected_trapped_tokens}, Got: {current_cf_balance_after_listing - initial_cf_pool_token_balance}"
    #     )

    #     # For completeness, let the OTC offer be taken and shares withdrawn
    #     otc_offer = self.con_otc.otc_listing[otc_listing_id]
    #     # Expected offer_amount on OTC: P_net = P(1-F_rate) = 100 * (1 - 0.1) = 90
    #     expected_otc_offer_amount = contribution_amount_p * (decimal('1.0') - fee_rate)
    #     self.assertEqual(otc_offer['offer_amount'], expected_otc_offer_amount)

    #     time_for_taking_offer = self._get_future_time(time_for_listing, minutes=30)
    #     # Charlie needs allowance for take_token + taker_fee
    #     # Taker fee = otc_total_take_amount * fee_rate = 500 * 0.1 = 50
    #     # Total needed by Charlie for take_token = 500 + 50 = 550. Charlie has 5000 in setUp.
    #     self.con_otc.take_offer(
    #         listing_id=otc_listing_id, 
    #         signer=self.charlie,
    #         environment={"now": time_for_taking_offer}
    #     )
        
    #     # Crowdfund contract should have received otc_total_take_amount
    #     self.assertEqual(
    #         self.con_otc_take_token.balance_of(address=self.crowdfund_contract_name),
    #         otc_total_take_amount,
    #         "Crowdfund did not receive the correct amount of take_tokens."
    #     )

    #     # Bob withdraws his share
    #     # Bob's initial take_token balance is 0 (from setUp).
    #     bob_initial_take_token_bal = self.con_otc_take_token.balance_of(address=self.bob)
    #     self.assertEqual(bob_initial_take_token_bal, decimal('0'))

    #     self.con_crowdfund_otc.withdraw_share(
    #         pool_id=pool_id, 
    #         signer=self.bob, 
    #         environment={"now": time_for_taking_offer}
    #     )
        
    #     # Bob should get all otc_total_take_amount as he was the sole contributor of the 100 tokens.
    #     self.assertEqual(
    #         self.con_otc_take_token.balance_of(address=self.bob),
    #         otc_total_take_amount,
    #         "Bob did not receive the correct share of take_tokens."
    #     )
        
    #     # Crowdfund contract should have 0 take_tokens left
    #     self.assertEqual(
    #         self.con_otc_take_token.balance_of(address=self.crowdfund_contract_name),
    #         decimal('0'),
    #         "Crowdfund should have no take_tokens left after shares are withdrawn."
    #     )
        
    #     # Crucially, the pool_token balance in crowdfund contract remains unchanged (still has trapped tokens)
    #     final_cf_pool_token_balance = self.con_pool_token.balance_of(address=self.crowdfund_contract_name)
    #     self.assertEqual(
    #         final_cf_pool_token_balance,
    #         expected_balance_after_listing, # This is initial_cf_pool_token_balance + expected_trapped_tokens
    #         "Trapped pool_tokens are still in the crowdfund contract after all operations."
    #     )
        
    #     print(f"Test confirmed: {expected_trapped_tokens} pool_tokens are trapped in the crowdfund contract for pool_id {pool_id}.")

     # --- This test case was used to demonstrate that there was the vulnerability of reentrancy ---
    # def test_reentrancy_withdraw_share_double_payment(self):
    #     print("\n--- Test: Re-entrancy in Withdraw Share (Double Payment) ---")
        
    #     malicious_token_contract_name = self.malicious_token_name 
    #     mt_address = malicious_token_contract_name 
        
    #     attacker_owner = self.operator 

    #     con_mt = self.client.get_contract(malicious_token_contract_name)

    #     pool_creation_time = self._get_future_time(self.base_time, hours=1)
    #     pool_id = self.con_crowdfund_otc.create_pool(
    #         description="Re-entrancy Withdraw Share Pool",
    #         pool_token=self.pool_token_name, 
    #         hard_cap=decimal('1000'),
    #         soft_cap=decimal('100'), 
    #         signer=self.alice, 
    #         environment={"now": pool_creation_time} # This sets 'now' for create_pool
    #     )

    #     contribution_amount_mt = decimal('100') 

    #     self.con_pool_token.transfer(amount=contribution_amount_mt, to=mt_address, signer=self.operator)
    #     self.assertEqual(self.con_pool_token.balance_of(address=mt_address), contribution_amount_mt)
        
    #     con_mt.execute_token_approve(
    #         token_contract_name=self.pool_token_name,
    #         spender=self.crowdfund_contract_name,
    #         amount=contribution_amount_mt,
    #         signer=attacker_owner 
    #     )
        
    #     # Define the time for the contribution
    #     contrib_time = self._get_future_time(pool_creation_time, minutes=10) # Well within 5 days

    #     # MODIFICATION: Pass the environment to the execute_contribute call
    #     con_mt.execute_contribute(
    #         crowdfund_contract_name=self.crowdfund_contract_name,
    #         pool_id=pool_id,
    #         amount=contribution_amount_mt,
    #         signer=attacker_owner,
    #         environment={"now": contrib_time} # Set 'now' for this transaction
    #     )
    #     self.assertEqual(self.con_crowdfund_otc.contributor[mt_address, pool_id]['amount_contributed'], contribution_amount_mt)
    #     self.assertEqual(self.con_crowdfund_otc.pool_fund[pool_id]['amount_received'], contribution_amount_mt)

    #     charlie_mt_balance = decimal('1000')
    #     con_mt.mint(amount=charlie_mt_balance, to=self.charlie, signer=attacker_owner)
    #     con_mt.approve(amount=charlie_mt_balance, to=self.otc_contract_name, signer=self.charlie)

    #     otc_total_take_amount_mt = decimal('200') 
        
    #     time_for_listing = self._get_future_time(contrib_time, days=6) # contrib_time + 6 days
    #                                                                    # = pool_creation_time + 10 mins + 6 days
    #                                                                    # This is > pool_creation_time + 5 days (contrib deadline)
    #                                                                    # which is correct for listing.

    #     self.con_crowdfund_otc.list_pooled_funds_on_otc(
    #         pool_id=pool_id,
    #         otc_take_token=malicious_token_contract_name, 
    #         otc_total_take_amount=otc_total_take_amount_mt,
    #         signer=self.alice, 
    #         environment={"now": time_for_listing} # Set 'now' for listing
    #     )
        
    #     otc_listing_id = self.con_crowdfund_otc.pool_fund[pool_id]['otc_listing_id']
    #     time_for_taking_offer = self._get_future_time(time_for_listing, minutes=30)
        
    #     self.con_otc.take_offer(
    #         listing_id=otc_listing_id,
    #         signer=self.charlie,
    #         environment={"now": time_for_taking_offer} # Set 'now' for taking offer
    #     )
        
    #     self.assertEqual(con_mt.balance_of(address=self.crowdfund_contract_name), otc_total_take_amount_mt)
        
    #     con_mt.configure_re_entrancy_for_withdraw(
    #         crowdfund_name=self.crowdfund_contract_name,
    #         pool_id=pool_id,
    #         signer=attacker_owner
    #     )
        
    #     con_mt.mint(amount=decimal('500'), to=self.crowdfund_contract_name, signer=attacker_owner)
        
    #     mt_balance_before_exploit_adjusted = con_mt.balance_of(address=mt_address)

    #     time_for_withdraw = self._get_future_time(time_for_taking_offer, minutes=5)
    #     con_mt.execute_withdraw_share(
    #         crowdfund_contract_name=self.crowdfund_contract_name,
    #         pool_id=pool_id,
    #         signer=attacker_owner,
    #         environment={"now": time_for_withdraw} # Set 'now' for withdraw
    #     )
    #     mt_balance_after_exploit_adjusted = con_mt.balance_of(address=mt_address)

    #     expected_single_share = otc_total_take_amount_mt 
        
    #     self.assertEqual(
    #         mt_balance_after_exploit_adjusted,
    #         mt_balance_before_exploit_adjusted + (expected_single_share * 2),
    #         "MT contract should have received its share twice."
    #     )

    #     self.assertEqual(con_mt.balance_of(address=self.crowdfund_contract_name), decimal('300'))
        
    #     self.assertTrue(self.con_crowdfund_otc.contributor[mt_address, pool_id]['share_withdrawn'])

if __name__ == '__main__':
    # This allows running the tests from the command line
    # You might need to adjust Python's path if contracting module is not found
    # e.g., by setting PYTHONPATH or running from the project root.
    # For simplicity, assuming tests are run in an environment where 'contracting' is accessible.
    
    # Create a TestLoader
    loader = unittest.TestLoader()
    
    # Load tests from the existing TestCrowdfundContract class (from the original file)
    # Assuming the original test file is named 'test.py' and class is 'TestCrowdfundContract'
    # This part is tricky if the original class is not imported here.
    # For a self-contained example, we'd redefine or import TestCrowdfundContract.
    # Since this is an extension, let's assume we can load it if it's in the same execution context.
    # If not, you'd run `python -m unittest test.py` and `python -m unittest this_file.py` separately,
    # or combine them into one file or use a test suite.

    # For now, let's just run the tests defined in *this* class:
    suite = unittest.TestSuite()
    suite.addTest(loader.loadTestsFromTestCase(TestCrowdfundContractMoreCases))
    
    # You could also add tests from the original class if it's imported:
    # from test import TestCrowdfundContract # Assuming original is test.py
    # suite.addTest(loader.loadTestsFromTestCase(TestCrowdfundContract))

    runner = unittest.TextTestRunner()
    runner.run(suite)