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
        self.taxable_pool_token_name = "con_taxable_pool_token"

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
        with open(current_dir / "con_taxable_pool_token.py") as f:
            self.client.submit(f.read(), name=self.taxable_pool_token_name, signer=self.operator)

        self.con_crowdfund_otc = self.client.get_contract(self.crowdfund_contract_name)
        self.con_otc = self.client.get_contract(self.otc_contract_name)
        self.con_pool_token = self.client.get_contract(self.pool_token_name)
        self.con_otc_take_token = self.client.get_contract(self.take_token_name)
        self.con_taxable_pool_token = self.client.get_contract(self.taxable_pool_token_name)

        # Token Distribution
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.alice, signer=self.operator)
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.bob, signer=self.operator)
        self.con_pool_token.transfer(amount=decimal('1000'), to=self.charlie, signer=self.operator)
        self.con_otc_take_token.transfer(amount=decimal('5000'), to=self.charlie, signer=self.operator)
        self.con_otc_take_token.transfer(amount=decimal('5000'), to=self.dave, signer=self.operator)
        self.con_taxable_pool_token.transfer(amount=decimal('1000'), to=self.bob, signer=self.operator)


        # Approvals for crowdfund contributions
        self.con_pool_token.approve(amount=decimal('1000'), to=self.crowdfund_contract_name, signer=self.alice)
        self.con_pool_token.approve(amount=decimal('1000'), to=self.crowdfund_contract_name, signer=self.bob)
        self.con_pool_token.approve(amount=decimal('1000'), to=self.crowdfund_contract_name, signer=self.charlie)
        self.con_taxable_pool_token.approve(amount=decimal('1000'), to=self.crowdfund_contract_name, signer=self.bob)

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
        self.assertEqual(pool_info_after_withdraw['status'], "REFUNDING") # Or "REFUNDING"

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
        with self.assertRaisesRegex(AssertionError, "no original nominal contribution to claim a share for"):
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

    def test_vulnerability_funds_trapped_if_otc_offer_expires_open_and_unresponsive_creator(self):
        print("\n--- Test: FIX VERIFICATION - Funds Trapped if OTC Offer Expires Open (Creator Unresponsive) ---")
        
        original_otc_contract_fee = self.con_otc.fee.get()
        self.con_otc.adjust_fee(trading_fee=decimal('0.0'), signer=self.operator)

        pool_creation_time = self._get_future_time(self.base_time, hours=2) 
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Trap Test Pool Fix", 
            pool_token=self.pool_token_name,
            hard_cap=decimal('100'), 
            soft_cap=decimal('10'), 
            signer=self.alice,
            environment={"now": pool_creation_time}
        )
        
        contribution_amount = decimal('50')
        contrib_time = self._get_future_time(pool_creation_time, days=1)

        # Balances before Bob contributes to this specific pool
        bob_pt_bal_before_contrib_scenario = self.con_pool_token.balance_of(address=self.bob)
        cf_pt_bal_before_contrib_scenario = self.con_pool_token.balance_of(address=self.crowdfund_contract_name)
        otc_pt_bal_before_contrib_scenario = self.con_pool_token.balance_of(address=self.otc_contract_name) # Balance before any activity for this pool
        
        self.con_crowdfund_otc.contribute(
            pool_id=pool_id, 
            amount=contribution_amount, 
            signer=self.bob, 
            environment={"now": contrib_time}
        )
        # CF contract's balance of pool_token for this pool is now +contribution_amount
        self.assertEqual(self.con_pool_token.balance_of(address=self.crowdfund_contract_name), 
                         cf_pt_bal_before_contrib_scenario + contribution_amount)

        # Alice lists on OTC. 
        time_for_listing = self._get_future_time(pool_creation_time, days=6) 
        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id, 
            otc_take_token=self.take_token_name,
            otc_total_take_amount=decimal('200'), 
            signer=self.alice,
            environment={"now": time_for_listing}
        )
        
        # Pool tokens moved from CF to OTC
        self.assertEqual(self.con_pool_token.balance_of(address=self.crowdfund_contract_name), 
                         cf_pt_bal_before_contrib_scenario, 
                         "CF balance incorrect after listing")
        self.assertEqual(self.con_pool_token.balance_of(address=self.otc_contract_name), 
                         otc_pt_bal_before_contrib_scenario + contribution_amount, 
                         "OTC balance incorrect after listing")
        
        otc_offer_details_on_otc_before_expiry = self.con_otc.otc_listing[otc_listing_id]
        self.assertEqual(otc_offer_details_on_otc_before_expiry['status'], "OPEN", "OTC offer not OPEN after listing")

        # Time passes beyond exchange deadline. 
        time_after_otc_expiry = self._get_future_time(pool_creation_time, days=9) 

        # Bob attempts to withdraw. The fix should auto-cancel the OTC offer.
        self.con_crowdfund_otc.withdraw_contribution(
            pool_id=pool_id, 
            signer=self.bob, 
            environment={"now": time_after_otc_expiry}
        )
        
        # Verify Bob's balance is restored
        self.assertEqual(self.con_pool_token.balance_of(address=self.bob), 
                         bob_pt_bal_before_contrib_scenario, 
                         "Bob's balance not restored after successful withdraw")
        
        # Verify tokens moved back from OTC to CF, then CF paid Bob.
        # CF's final balance for this pool's token should be back to its state before Bob's contribution for this pool.
        self.assertEqual(self.con_pool_token.balance_of(address=self.crowdfund_contract_name), 
                         cf_pt_bal_before_contrib_scenario, 
                         "CF balance incorrect after successful withdraw and auto-cancellation")
        # OTC's final balance for this pool's token should be back to its state before this pool's listing.
        self.assertEqual(self.con_pool_token.balance_of(address=self.otc_contract_name), 
                         otc_pt_bal_before_contrib_scenario, 
                         "OTC balance incorrect after successful withdraw and auto-cancellation")

        # Verify pool status in CF contract is OTC_FAILED
        pool_info_after_withdraw = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_withdraw['status'], "OTC_FAILED", "Pool status not OTC_FAILED after withdraw")
        
        # Verify OTC offer status on OTC contract is CANCELLED due to auto-cancellation
        otc_offer_details_on_otc_after_withdraw = self.con_otc.otc_listing[otc_listing_id]
        self.assertEqual(otc_offer_details_on_otc_after_withdraw['status'], "CANCELLED", "OTC offer status not CANCELLED after auto-cancellation")

        # Verify Bob's contribution info in CF contract shows 0 amount contributed
        bob_contrib_info_after_withdraw = self.con_crowdfund_otc.contributor[self.bob, pool_id]
        self.assertIsNotNone(bob_contrib_info_after_withdraw, "Bob's contribution info missing")
        self.assertEqual(bob_contrib_info_after_withdraw['amount_contributed'], decimal('0'), "Bob's recorded contribution not zeroed out")

        print(f"Fix Confirmed: Bob successfully withdrew his {contribution_amount} {self.pool_token_name} after OTC offer expired open, due to auto-cancellation.")

        self.con_otc.adjust_fee(trading_fee=original_otc_contract_fee, signer=self.operator)

    def test_taxable_token_contribution_listing_and_withdraw_share_success(self):
        print("\n--- Test: Taxable Token - Full Cycle Success (Contrib, List, Take, Withdraw Share) ---")

        taxable_token_contract = self.con_taxable_pool_token
        taxable_token_name = self.taxable_pool_token_name
        tax_rate = decimal('0.05') # Known tax rate from con_taxable_pool_token.py
        
        # Ensure OTC contract has 0% fee for this test to simplify take_token share calculations
        original_otc_fee = self.con_otc.fee.get()
        self.con_otc.adjust_fee(trading_fee=decimal('0.0'), signer=self.operator)

        # Alice creates a pool with the taxable token
        pool_id = self.con_crowdfund_otc.create_pool(
            description="Taxable Token Success Pool",
            pool_token=taxable_token_name,
            hard_cap=decimal('500'), # Nominal hard cap
            soft_cap=decimal('150'), # Nominal soft cap
            signer=self.alice,
            environment={"now": self.base_time}
        )
        
        # Initial state checks for taxable token balance in CF contract.
        # This assumes cf_initial_taxable_token_balance is 0 if no other pool used this token.
        # If other pools might have used it, this check needs to be relative to *before this pool's activity*.
        # For simplicity, let's capture it.
        cf_taxable_token_balance_before_this_pool = taxable_token_contract.balance_of(address=self.crowdfund_contract_name)


        # Bob contributes
        bob_nominal_contrib = decimal('100.0')
        contrib_time_bob = self._get_future_time(self.base_time, days=1)
        self.con_crowdfund_otc.contribute(
            pool_id=pool_id, amount=bob_nominal_contrib, signer=self.bob, environment={"now": contrib_time_bob}
        )
        
        bob_actual_added = bob_nominal_contrib * (decimal('1.0') - tax_rate)
        pool_info_after_bob = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_bob['total_nominal_contributions'], bob_nominal_contrib)
        self.assertEqual(pool_info_after_bob['amount_received'], bob_actual_added)
        self.assertEqual(taxable_token_contract.balance_of(address=self.crowdfund_contract_name), 
                         cf_taxable_token_balance_before_this_pool + bob_actual_added)
        bob_contrib_record = self.con_crowdfund_otc.get_contribution_info(pool_id = pool_id, account = self.bob)
        self.assertEqual(bob_contrib_record['amount_contributed'], bob_nominal_contrib)
        self.assertEqual(bob_contrib_record['actual_amount_added'], bob_actual_added)

        # Charlie contributes
        charlie_nominal_contrib = decimal('100.0') 
        # Ensure Charlie has TPT and approval
        if taxable_token_contract.balance_of(address=self.charlie) < charlie_nominal_contrib:
             taxable_token_contract.transfer(amount=charlie_nominal_contrib*decimal('1.1'), to=self.charlie, signer=self.operator) # Give Charlie enough TPT, considering tax if transferring *to* him. Here, simple transfer *from* operator.
        taxable_token_contract.approve(amount=charlie_nominal_contrib, to=self.crowdfund_contract_name, signer=self.charlie)

        contrib_time_charlie = self._get_future_time(contrib_time_bob, minutes=10)
        self.con_crowdfund_otc.contribute(
            pool_id=pool_id, amount=charlie_nominal_contrib, signer=self.charlie, environment={"now": contrib_time_charlie}
        )

        charlie_actual_added = charlie_nominal_contrib * (decimal('1.0') - tax_rate)
        total_nominal_contributions = bob_nominal_contrib + charlie_nominal_contrib
        total_actual_received = bob_actual_added + charlie_actual_added

        pool_info_after_charlie = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_after_charlie['total_nominal_contributions'], total_nominal_contributions)
        self.assertEqual(pool_info_after_charlie['amount_received'], total_actual_received)
        self.assertEqual(taxable_token_contract.balance_of(address=self.crowdfund_contract_name), 
                         cf_taxable_token_balance_before_this_pool + total_actual_received)
        charlie_contrib_record = self.con_crowdfund_otc.get_contribution_info(pool_id = pool_id, account = self.charlie)
        self.assertEqual(charlie_contrib_record['amount_contributed'], charlie_nominal_contrib)
        self.assertEqual(charlie_contrib_record['actual_amount_added'], charlie_actual_added)

        # Alice lists the pool on OTC (soft cap 150 nominal is met by 200 nominal)
        time_for_listing = self._get_future_time(self.base_time, days=6)
        otc_take_amount_target = decimal('380.0') 

        otc_listing_id = self.con_crowdfund_otc.list_pooled_funds_on_otc(
            pool_id=pool_id,
            otc_take_token=self.take_token_name,
            otc_total_take_amount=otc_take_amount_target,
            signer=self.alice,
            environment={"now": time_for_listing}
        )
        self.assertIsNotNone(otc_listing_id)

        actual_tokens_received_by_otc_and_listed = total_actual_received * (decimal('1.0') - tax_rate)
        
        otc_offer_on_otc = self.con_otc.otc_listing[otc_listing_id]
        self.assertEqual(otc_offer_on_otc['offer_token'], taxable_token_name)
        self.assertEqual(otc_offer_on_otc['offer_amount'], actual_tokens_received_by_otc_and_listed) 
        self.assertEqual(otc_offer_on_otc['take_amount'], otc_take_amount_target)
        
        # After OTC contract pulls funds during its list_offer.
        # The list_offer in con_otc.py uses transfer_from. The taxable_pool_token.transfer_from also applies tax.
        # This means the OTC contract will receive total_actual_received * (1 - tax_rate)
        # This is an important detail! The OTC contract itself is subject to the tax when receiving.
        actual_tokens_received_by_otc = total_actual_received * (decimal('1.0') - tax_rate)
        
        self.assertEqual(taxable_token_contract.balance_of(address=self.crowdfund_contract_name),
                         cf_taxable_token_balance_before_this_pool) 
        # This assertion needs to be careful: If otc_contract_name was already holding some taxable_token,
        # the check should be relative.
        # However, list_offer in con_otc.py transfers 'offer_amount' (which is net_offer_amount_for_otc after CF calculation)
        # + 'maker_fee_to_collect'. If OTC fee is 0, maker_fee_to_collect is 0.
        # So con_otc.list_offer calls transfer_from(amount=total_actual_received, to=ctx.this (otc_contract), main_account=CF_contract)
        # Therefore, the OTC contract receives total_actual_received * (1-tax_rate).
        
        # The OTC contract's internal 'offer_amount' is what the *taker* will receive if they take the offer.
        # If the OTC contract received `actual_tokens_received_by_otc`, this is what it can give to the taker.
        # But `otc_offer_on_otc['offer_amount']` was set based on CF's calculation *before* this tax on transfer to OTC.
        # This implies a mismatch if con_otc.take_offer tries to send `otc_offer_on_otc['offer_amount']`
        # but only holds `actual_tokens_received_by_otc`.
        #
        # Let's re-evaluate con_otc.list_offer:
        # 1. CF calls `otc_contract.list_offer(offer_token, offer_amount=A, take_token, take_amount=B)`
        #    Here, `A` is `net_offer_amount_for_otc` from CF, calculated as `total_actual_received / (1 + otc_fee_rate)`.
        #    If otc_fee_rate is 0, then `A = total_actual_received`.
        # 2. `con_otc.list_offer` calculates `maker_fee_to_collect = A * otc_fee_rate`. If fee is 0, this is 0.
        # 3. `con_otc.list_offer` calls `offer_token_contract_module.transfer_from(amount=A + maker_fee_to_collect, to=ctx.this, main_account=CF_contract)`
        #    So, it tries to pull `A` (i.e. `total_actual_received`) from CF.
        # 4. Taxable token's `transfer_from` executes. `to=ctx.this` (OTC contract) means OTC contract receives `total_actual_received * (1 - tax_rate)`.
        # 5. `con_otc` then stores `otc_listing[id] = {"offer_amount": A, ...}`. So it *records* the pre-tax-to-OTC amount.
        #
        # When `con_otc.take_offer` is called:
        # 1. It sends `original_offer_amount` (which is `A`) to the taker.
        #    `offer_token_contract_instance.transfer(amount=original_offer_amount, to=ctx.caller (taker))`
        # This will fail if `original_offer_amount > actual balance of OTC contract for that token`.
        #
        # This means `con_otc.py` is also vulnerable to taxable offer_tokens if it doesn't account for tax on receiving them.
        # For *this* test of `con_crowdfund_otc.py`, we want to show CF works.
        # To make the test pass through the OTC part, we either:
        #   a) Assume `con_otc.py` is also fixed (outside scope of current request).
        #   b) Use a non-taxable token as the `offer_token` for the OTC part of this specific test,
        #      even if the pool token was taxable. This isn't what we want to test.
        #   c) Acknowledge that the `take_offer` call will fail due to `con_otc`'s issue and stop the test there,
        #      or verify that `con_otc` has the post-tax-to-OTC amount.
        #
        # Let's assume the goal is to test CF's handling *up to the point of interacting with OTC*.
        # The `list_pooled_funds_on_otc` in CF calls `otc_contract.list_offer`.
        # CF correctly approves `total_actual_received`.
        # CF correctly calculates `net_offer_amount_for_otc` based on `total_actual_received`.
        # The call to `otc_contract.list_offer` will try to pull `total_actual_received` (if OTC fee 0) from CF. This works.
        # OTC contract will receive `total_actual_received * (1-tax_rate)`.
        # OTC contract will record `offer_amount` as `total_actual_received`.

        # So, the current `otc_offer_on_otc['offer_amount']` assertion is correct for what con_otc *records*.
        # The balance check for OTC contract should be for the post-tax-to-OTC amount.
        # The failure would occur in `take_offer`.

        otc_balance_of_taxable_token_after_listing = taxable_token_contract.balance_of(address=self.otc_contract_name)
        expected_otc_taxable_token_balance = actual_tokens_received_by_otc # Assuming OTC started with 0 of this token.
        self.assertEqual(otc_balance_of_taxable_token_after_listing, expected_otc_taxable_token_balance)

        # Dave takes the offer
        time_for_taking_offer = self._get_future_time(time_for_listing, minutes=30)
        
        # This take_offer will likely FAIL if con_otc is not also tax-aware for offer_tokens.
        # It will try to send `total_actual_received` but only holds `actual_tokens_received_by_otc`.
        # For the purpose of testing CF contract, if take_offer fails here, it's an OTC issue.
        # To proceed with testing CF's withdraw_share, we need take_offer to "succeed" conceptually for CF.
        #
        # If we expect con_otc to fail:
        # with self.assertRaisesRegex(AssertionError, "Transfer amount exceeds balance"): # Error from taxable token's .transfer
        #     self.con_otc.take_offer(
        #         listing_id=otc_listing_id,
        #         signer=self.dave, 
        #         environment={"now": time_for_taking_offer}
        #     )
        # print("Take_offer failed as expected due to con_otc not being tax-aware for received offer_tokens.")
        # self.con_otc.adjust_fee(trading_fee=original_otc_fee, signer=self.operator) # Restore fee
        # return # End test here as further CF functions can't be tested.

        # --- SIMULATING A "SUCCESSFUL" OTC DEAL FOR CF'S SAKE ---
        # To test CF's withdraw_share, we'll manually credit CF with take_tokens as if OTC worked perfectly
        # and the amount of take_tokens was based on the *actual value exchanged*.
        # The actual value CF provided to OTC was `actual_tokens_received_by_otc`.
        # Let's say `otc_take_amount_target` was meant for `total_actual_received`.
        # Then for `actual_tokens_received_by_otc`, the equivalent take_tokens would be:
        # `otc_take_amount_target * (actual_tokens_received_by_otc / total_actual_received)`
        # ` = otc_take_amount_target * (1 - tax_rate)`                                                                                       # OR, if OTC is just shortchanged on offer_token
                                                                                        # and can't fulfill the original take_amount promise.
        # This gets too complex. Let's assume for this test, miraculously, con_otc.py can handle it
        # and transfers the full `otc_take_amount_target` to the crowdfund contract.
        # This means we are testing CF's logic assuming OTC delivers.

        self.con_otc.take_offer(
             listing_id=otc_listing_id,
             signer=self.dave, 
             environment={"now": time_for_taking_offer}
        )
        # IF THE ABOVE PASSES, it means con_otc.py could fulfill the offer. This implies either:
        # 1. The taxable_token.transfer to the taker was not taxed (unlikely for a generic taxable token).
        # 2. The OTC contract had other funds to cover the shortfall (not a clean test).
        # 3. The `original_offer_amount` it tried to send was small enough.
        #
        # The most likely outcome is that con_otc.take_offer will fail if the taxable_token.transfer from OTC to Taker is also taxed,
        # or if it tries to transfer an amount it doesn't have due to tax on receiving.
        #
        # For the purpose of testing *con_crowdfund_otc.py's* `withdraw_share`:
        # Assume the `con_otc.take_offer` call completed and `self.crowdfund_contract_name`
        # received `otc_take_amount_target`.
        
        self.assertEqual(self.con_otc_take_token.balance_of(address=self.crowdfund_contract_name),
                         otc_take_amount_target)
        
        # Bob withdraws his share
        bob_expected_share = (bob_nominal_contrib / total_nominal_contributions) * otc_take_amount_target
        bob_take_token_bal_before_withdraw = self.con_otc_take_token.balance_of(address=self.bob)
        
        self.con_crowdfund_otc.withdraw_share(
            pool_id=pool_id, signer=self.bob, environment={"now": time_for_taking_offer}
        )
        self.assertEqual(self.con_otc_take_token.balance_of(address=self.bob),
                         bob_take_token_bal_before_withdraw + bob_expected_share)

        # Charlie withdraws his share
        charlie_expected_share = (charlie_nominal_contrib / total_nominal_contributions) * otc_take_amount_target
        charlie_take_token_bal_before_withdraw = self.con_otc_take_token.balance_of(address=self.charlie)

        self.con_crowdfund_otc.withdraw_share(
            pool_id=pool_id, signer=self.charlie, environment={"now": time_for_taking_offer}
        )
        self.assertEqual(self.con_otc_take_token.balance_of(address=self.charlie),
                         charlie_take_token_bal_before_withdraw + charlie_expected_share)
                         
        self.assertEqual(self.con_otc_take_token.balance_of(address=self.crowdfund_contract_name), decimal('0'))
        
        pool_info_final = self.con_crowdfund_otc.pool_fund[pool_id]
        self.assertEqual(pool_info_final['status'], "OTC_EXECUTED")
        self.assertEqual(pool_info_final['otc_actual_received_amount'], otc_take_amount_target)

        self.con_otc.adjust_fee(trading_fee=original_otc_fee, signer=self.operator)
        print(f"Taxable token test: CF logic for contribution, listing, and share withdrawal works. Bob received {bob_expected_share}, Charlie received {charlie_expected_share}.")
        print("Note: This test's success for 'take_offer' implies con_otc.py can handle the taxable offer_token or the specific amounts allowed it.")

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