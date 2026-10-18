from __future__ import annotations

import unittest

from monopoly.engine import MonopolyGame
from monopoly.models import DeckKind, GamePhase, InvalidActionError, TradeOffer


class GameSetupTests(unittest.TestCase):
    def test_requires_two_to_four_unique_players(self) -> None:
        for names in ([], ["Solo"], ["A", "B", "C", "D", "E"], ["Ala", "ala"]):
            with self.subTest(names=names), self.assertRaises(InvalidActionError):
                MonopolyGame.new(names)

    def test_new_game_has_classic_starting_state(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek"], seed=42)

        self.assertEqual([player.cash for player in game.state.players], [1_500, 1_500])
        self.assertEqual(len(game.state.properties), 28)
        self.assertEqual(len(game.state.decks[DeckKind.CHANCE].cards), 16)
        self.assertEqual(len(game.state.decks[DeckKind.COMMUNITY_CHEST].cards), 16)
        game.validate_invariants()


class MovementAndPurchaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = MonopolyGame.new(["Ala", "Bartek"], seed=7)

    def test_landing_on_unowned_property_requires_purchase_decision(self) -> None:
        self.game.roll_dice((1, 2))

        self.assertEqual(self.game.current_player.position, 3)
        self.assertEqual(self.game.state.phase, GamePhase.AWAITING_PURCHASE)
        self.assertEqual(self.game.state.pending_property, 3)

        self.game.buy_pending_property()
        self.assertEqual(self.game.current_player.cash, 1_440)
        self.assertEqual(self.game.state.properties[3].owner_id, self.game.current_player.id)

    def test_auction_can_be_won_by_another_player(self) -> None:
        self.game.roll_dice((1, 2))
        winner = self.game.state.players[1]

        self.game.resolve_auction(winner.id, 17)

        self.assertEqual(self.game.state.properties[3].owner_id, winner.id)
        self.assertEqual(winner.cash, 1_483)

    def test_unaffordable_auction_result_is_rejected_without_mutation(self) -> None:
        self.game.roll_dice((1, 2))
        winner = self.game.state.players[1]
        winner.cash = 0

        with self.assertRaises(InvalidActionError):
            self.game.resolve_auction(winner.id, 1)

        self.assertFalse(winner.bankrupt)
        self.assertEqual(self.game.state.phase, GamePhase.AWAITING_PURCHASE)
        self.assertEqual(self.game.state.pending_property, 3)
        self.assertIsNone(self.game.state.properties[3].owner_id)

    def test_dice_must_be_real_integers(self) -> None:
        for dice in ((True, True), (1.0, 2.0)):
            with self.subTest(dice=dice), self.assertRaises(InvalidActionError):
                self.game.roll_dice(dice)  # type: ignore[arg-type]
        self.assertEqual(self.game.current_player.position, 0)
        self.assertIsNone(self.game.state.last_roll)

    def test_bankrupt_players_properties_are_auctioned_by_the_bank(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek", "Celina"], seed=7)
        alice, bob, _ = game.state.players
        alice.cash = 0
        alice.position = 1
        game.state.properties[5].owner_id = alice.id
        game.state.properties[6].owner_id = alice.id

        game.roll_dice((1, 2))  # Income Tax bankrupts Ala.

        self.assertTrue(alice.bankrupt)
        self.assertEqual(game.state.phase, GamePhase.AWAITING_AUCTION)
        self.assertEqual(game.state.pending_property, 5)
        self.assertEqual(game.state.bank_auction_queue, [6])
        game.validate_invariants()

        game.resolve_auction(None)
        self.assertEqual(game.state.pending_property, 6)
        game.resolve_auction(bob.id, 1)

        self.assertEqual(game.state.properties[6].owner_id, bob.id)
        self.assertEqual(game.state.phase, GamePhase.ACTIONS)
        game.end_turn()
        self.assertEqual(game.current_player, bob)

    def test_passing_go_pays_salary(self) -> None:
        player = self.game.current_player
        player.position = 39

        self.game.roll_dice((1, 1))

        self.assertEqual(player.position, 1)
        self.assertEqual(player.cash, 1_700)

    def test_third_consecutive_double_sends_player_to_jail_without_move(self) -> None:
        self.game.roll_dice((3, 3))
        self.game.resolve_auction(None)
        self.game.end_turn()
        self.game.roll_dice((2, 2))
        self.game.end_turn()

        self.game.roll_dice((1, 1))

        player = self.game.current_player
        self.assertTrue(player.in_jail)
        self.assertEqual(player.position, 10)
        self.assertEqual(self.game.state.phase, GamePhase.ACTIONS)
        self.assertFalse(self.game.state.extra_turn)

    def test_third_failed_jail_roll_charges_and_moves(self) -> None:
        player = self.game.current_player
        player.position = 10
        player.in_jail = True
        player.jail_turns = 2

        self.game.roll_dice((1, 2))

        self.assertFalse(player.in_jail)
        self.assertEqual(player.position, 13)
        self.assertEqual(player.cash, 1_450)

    def test_jail_fee_bankruptcy_can_advance_a_three_player_game(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek", "Celina"], seed=7)
        player = game.current_player
        player.position = 10
        player.in_jail = True
        player.jail_turns = 2
        player.cash = 0

        game.roll_dice((1, 2))

        self.assertTrue(player.bankrupt)
        self.assertEqual(game.state.phase, GamePhase.ACTIONS)
        game.end_turn()
        self.assertEqual(game.current_player.name, "Bartek")
        self.assertEqual(game.state.phase, GamePhase.AWAITING_ROLL)

    def test_player_cannot_bankrupt_themselves_by_attempting_unaffordable_purchase(self) -> None:
        self.game.current_player.cash = 0
        self.game.roll_dice((1, 2))

        with self.assertRaises(InvalidActionError):
            self.game.buy_pending_property()

        self.assertFalse(self.game.current_player.bankrupt)
        self.assertEqual(self.game.state.phase, GamePhase.AWAITING_PURCHASE)


class RentAndAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = MonopolyGame.new(["Ala", "Bartek"], seed=11)
        self.alice, self.bob = self.game.state.players

    def test_color_monopoly_doubles_unimproved_rent(self) -> None:
        self.game.state.properties[1].owner_id = self.bob.id
        self.game.state.properties[3].owner_id = self.bob.id
        self.alice.position = 39

        self.game.roll_dice((1, 1))

        self.assertEqual(self.alice.cash, 1_696)  # +200 for GO, then $4 rent
        self.assertEqual(self.bob.cash, 1_504)

    def test_utility_rent_uses_dice_and_number_owned(self) -> None:
        self.game.state.properties[12].owner_id = self.bob.id
        self.alice.position = 8

        self.game.roll_dice((2, 2))

        self.assertEqual(self.alice.cash, 1_484)
        self.assertEqual(self.bob.cash, 1_516)

    def test_building_is_even_and_updates_bank_inventory(self) -> None:
        self.game.state.phase = GamePhase.ACTIONS
        self.game.state.properties[1].owner_id = self.alice.id
        self.game.state.properties[3].owner_id = self.alice.id

        self.game.build(self.alice.id, 1)
        with self.assertRaises(InvalidActionError):
            self.game.build(self.alice.id, 1)
        self.game.build(self.alice.id, 3)

        self.assertEqual(self.game.state.bank_houses, 30)
        self.assertEqual(self.alice.cash, 1_400)
        self.game.validate_invariants()

    def test_mortgage_and_unmortgage_include_interest(self) -> None:
        self.game.state.phase = GamePhase.ACTIONS
        self.game.state.properties[5].owner_id = self.alice.id

        self.game.mortgage(self.alice.id, 5)
        self.assertEqual(self.alice.cash, 1_600)
        self.game.unmortgage(self.alice.id, 5)

        self.assertEqual(self.alice.cash, 1_490)
        self.assertFalse(self.game.state.properties[5].mortgaged)

    def test_trade_is_atomic_and_charges_mortgage_interest(self) -> None:
        self.game.state.phase = GamePhase.ACTIONS
        self.game.state.properties[5].owner_id = self.alice.id
        self.game.state.properties[5].mortgaged = True
        self.game.state.properties[6].owner_id = self.bob.id

        self.game.execute_trade(
            TradeOffer(
                proposer_id=self.alice.id,
                recipient_id=self.bob.id,
                offered_cash=25,
                requested_cash=10,
                offered_properties=(5,),
                requested_properties=(6,),
            )
        )

        self.assertEqual(self.game.state.properties[5].owner_id, self.bob.id)
        self.assertEqual(self.game.state.properties[6].owner_id, self.alice.id)
        self.assertEqual(self.alice.cash, 1_485)
        self.assertEqual(self.bob.cash, 1_505)  # includes $10 immediate mortgage interest

    def test_trade_rejects_noninteger_amounts_and_property_positions(self) -> None:
        invalid_offers = (
            TradeOffer(self.alice.id, self.bob.id, offered_cash=1.5),  # type: ignore[arg-type]
            TradeOffer(self.alice.id, self.bob.id, offered_cash=True),  # type: ignore[arg-type]
            TradeOffer(
                self.alice.id,
                self.bob.id,
                offered_properties=(True,),  # type: ignore[arg-type]
            ),
        )

        for offer in invalid_offers:
            with self.subTest(offer=offer), self.assertRaises(InvalidActionError):
                self.game.execute_trade(offer)
            self.assertEqual((self.alice.cash, self.bob.cash), (1_500, 1_500))

        self.game.validate_invariants()

    def test_trade_can_immediately_pay_off_transferred_mortgage(self) -> None:
        self.game.state.phase = GamePhase.ACTIONS
        self.game.state.properties[5].owner_id = self.alice.id
        self.game.state.properties[5].mortgaged = True

        self.game.execute_trade(
            TradeOffer(
                proposer_id=self.alice.id,
                recipient_id=self.bob.id,
                offered_properties=(5,),
                unmortgage_on_transfer=(5,),
            )
        )

        self.assertEqual(self.game.state.properties[5].owner_id, self.bob.id)
        self.assertFalse(self.game.state.properties[5].mortgaged)
        self.assertEqual(self.bob.cash, 1_390)  # $100 principal + $10 interest

    def test_bankruptcy_ends_two_player_game(self) -> None:
        self.alice.cash = 0
        self.alice.position = 36
        self.game.state.properties[39].owner_id = self.bob.id

        self.game.roll_dice((1, 2))

        self.assertTrue(self.alice.bankrupt)
        self.assertEqual(self.game.state.phase, GamePhase.GAME_OVER)
        self.assertEqual(self.game.state.winner_id, self.bob.id)


class CardTests(unittest.TestCase):
    def test_go_back_three_resolves_destination_card(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek"], seed=3)
        player = game.current_player
        player.position = 33
        chance = game.state.decks[DeckKind.CHANCE].cards
        chance.insert(0, chance.pop(chance.index("chance_back_3")))
        chest = game.state.decks[DeckKind.COMMUNITY_CHEST].cards
        chest.insert(0, chest.pop(chest.index("chest_bank_error")))

        game.roll_dice((1, 2))

        self.assertEqual(player.position, 33)
        self.assertEqual(player.cash, 1_700)
        self.assertEqual(game.state.phase, GamePhase.ACTIONS)
        game.validate_invariants()

    def test_collect_from_each_stops_if_collector_goes_bankrupt(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek", "Celina"], seed=5)
        alice, bob, celina = game.state.players
        alice.cash = 0
        bob.cash = 0
        celina.cash = 100
        game.state.properties[5].owner_id = bob.id
        game.state.properties[5].mortgaged = True
        chest = game.state.decks[DeckKind.COMMUNITY_CHEST].cards
        chest.insert(0, chest.pop(chest.index("chest_birthday")))

        game.roll_dice((1, 1))

        self.assertTrue(alice.bankrupt)
        self.assertTrue(bob.bankrupt)
        self.assertEqual(celina.cash, 100)
        self.assertEqual(game.state.winner_id, celina.id)
        self.assertEqual(
            sum("wins the game" in event for event in game.state.history),
            1,
        )
        game.validate_invariants()


if __name__ == "__main__":
    unittest.main()
