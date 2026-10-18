from __future__ import annotations

import random
import unicodedata
import uuid
from collections import Counter
from collections.abc import Iterable

from monopoly.board import BOARD, OWNABLE_POSITIONS, SPACE_BY_POSITION, spaces_in_group
from monopoly.cards import CARD_BY_ID, Card, CardAction, card_ids
from monopoly.models import (
    ColorGroup,
    DeckKind,
    DeckState,
    DiceRoll,
    GamePhase,
    GameState,
    InvalidActionError,
    Player,
    PropertyState,
    Space,
    SpaceKind,
    TradeOffer,
)
from monopoly.rules import CLASSIC_RULES, ClassicRules


class MonopolyGame:
    """The domain engine for a local 2-4 player game.

    The engine owns every state transition. User interfaces may inspect the
    state, but should only mutate it by calling these methods.
    """

    def __init__(
        self,
        state: GameState,
        *,
        rng: random.Random | None = None,
        rules: ClassicRules = CLASSIC_RULES,
    ) -> None:
        self.state = state
        self.rules = rules
        self.rng = rng or random.Random()
        self.revision = 0
        self.validate_invariants()

    @classmethod
    def new(
        cls,
        player_names: Iterable[str],
        *,
        seed: int | None = None,
        rules: ClassicRules = CLASSIC_RULES,
    ) -> MonopolyGame:
        raw_names = list(player_names)
        if any(not isinstance(name, str) for name in raw_names):
            raise InvalidActionError("Player names must be strings")
        names = [name.strip() for name in raw_names]
        if not rules.min_players <= len(names) <= rules.max_players:
            raise InvalidActionError(
                f"A game requires {rules.min_players}-{rules.max_players} players"
            )
        if any(not name for name in names):
            raise InvalidActionError("Player names cannot be empty")
        if any(_contains_terminal_control(name) for name in names):
            raise InvalidActionError("Player names cannot contain control characters")
        if len({name.casefold() for name in names}) != len(names):
            raise InvalidActionError("Player names must be unique")

        rng = random.Random(seed)
        players = [
            Player(id=str(uuid.uuid4()), name=name, cash=rules.starting_cash) for name in names
        ]
        decks: dict[DeckKind, DeckState] = {}
        for deck_kind in DeckKind:
            cards = card_ids(deck_kind)
            rng.shuffle(cards)
            decks[deck_kind] = DeckState(cards)

        state = GameState(
            game_id=str(uuid.uuid4()),
            players=players,
            properties={position: PropertyState() for position in OWNABLE_POSITIONS},
            decks=decks,
            bank_houses=rules.bank_houses,
            bank_hotels=rules.bank_hotels,
        )
        game = cls(state, rng=rng, rules=rules)
        game._log(f"Game started: {', '.join(names)}.")
        return game

    @property
    def current_player(self) -> Player:
        return self.state.current_player

    def player_by_id(self, player_id: str) -> Player:
        for player in self.state.players:
            if player.id == player_id:
                return player
        raise InvalidActionError("Unknown player")

    def assets_of(self, player_id: str) -> tuple[int, ...]:
        return tuple(
            position
            for position, asset in sorted(self.state.properties.items())
            if asset.owner_id == player_id
        )

    def roll_dice(self, dice: tuple[int, int] | None = None) -> DiceRoll:
        self._require_phase(GamePhase.AWAITING_ROLL)
        player = self.current_player
        if not player.active:
            raise InvalidActionError("A bankrupt player cannot roll")

        first, second = dice or (self.rng.randint(1, 6), self.rng.randint(1, 6))
        if (
            type(first) is not int
            or type(second) is not int
            or first not in range(1, 7)
            or second not in range(1, 7)
        ):
            raise InvalidActionError("Each die must have a value from 1 to 6")
        roll = DiceRoll(first, second)
        self.state.last_roll = roll
        self._log(f"{player.name} rolled {first} + {second} = {roll.total}.")

        if player.in_jail:
            self._resolve_jail_roll(player, roll)
            return roll

        if roll.is_double:
            self.state.doubles_in_turn += 1
            if self.state.doubles_in_turn == 3:
                self._log(f"{player.name} rolled three doubles and goes to Jail.")
                self._send_to_jail(player)
                return roll
            self.state.extra_turn = True
        else:
            self.state.extra_turn = False

        self._move_by(player, roll.total)
        self._resolve_space(player, dice_total=roll.total)
        return roll

    def pay_jail_fee(self) -> None:
        self._require_phase(GamePhase.AWAITING_ROLL)
        player = self.current_player
        if not player.in_jail:
            raise InvalidActionError("The current player is not in Jail")
        self._pay(player, self.rules.jail_fee, reason="Jail fee")
        if player.active:
            player.in_jail = False
            player.jail_turns = 0
            self._log(f"{player.name} paid ${self.rules.jail_fee} and left Jail.")
        elif self.state.phase not in {GamePhase.GAME_OVER, GamePhase.AWAITING_AUCTION}:
            # Bankruptcy can occur when a player voluntarily tries to pay.
            # ACTIONS lets the turn be closed and play continue.
            self.state.phase = GamePhase.ACTIONS

    def use_jail_card(self, deck: DeckKind | None = None) -> DeckKind:
        self._require_phase(GamePhase.AWAITING_ROLL)
        player = self.current_player
        if not player.in_jail:
            raise InvalidActionError("The current player is not in Jail")
        available = [kind for kind in DeckKind if player.jail_cards[kind] > 0]
        if deck is None:
            if not available:
                raise InvalidActionError("The player has no Get Out of Jail Free card")
            deck = available[0]
        if player.jail_cards.get(deck, 0) < 1:
            raise InvalidActionError(f"The player has no card from the {deck.value} deck")

        player.jail_cards[deck] -= 1
        held_card_id = (
            "chance_jail_free" if deck == DeckKind.CHANCE else "chest_jail_free"
        )
        self.state.decks[deck].cards.append(held_card_id)
        player.in_jail = False
        player.jail_turns = 0
        self._log(f"{player.name} used a Get Out of Jail Free card.")
        return deck

    def buy_pending_property(self) -> Space:
        self._require_phase(GamePhase.AWAITING_PURCHASE)
        position = self._pending_position()
        space = SPACE_BY_POSITION[position]
        player = self.current_player
        asset = self.state.properties[position]
        if asset.owner_id is not None:
            raise InvalidActionError("This property is no longer available")
        if player.cash < space.price:
            raise InvalidActionError(
                "The player needs enough cash for the purchase; manage assets or start an auction"
            )

        self._pay(player, space.price, reason=f"purchase of {space.name}")
        if player.active:
            asset.owner_id = player.id
            self._log(f"{player.name} bought {space.name} for ${space.price}.")
        self.state.pending_property = None
        if self.state.phase != GamePhase.GAME_OVER:
            self.state.phase = GamePhase.ACTIONS
        return space

    def resolve_auction(self, winner_id: str | None, bid: int = 0) -> Space:
        """Resolve an auction conducted by a UI.

        The engine validates the result and performs the atomic transfer. A UI
        may use an open ascending auction; passing ``None`` represents no bids.
        """

        auction_phase = self.state.phase
        self._require_phase(GamePhase.AWAITING_PURCHASE, GamePhase.AWAITING_AUCTION)
        position = self._pending_position()
        space = SPACE_BY_POSITION[position]
        asset = self.state.properties[position]
        if asset.owner_id is not None:
            raise InvalidActionError("This property is no longer available")
        if winner_id is None:
            if bid != 0:
                raise InvalidActionError("An auction without a winner must have a zero bid")
            self._log(f"No one bid for {space.name}; it remains with the Bank.")
        else:
            winner = self.player_by_id(winner_id)
            if not winner.active:
                raise InvalidActionError("A bankrupt player cannot win an auction")
            if bid < 1:
                raise InvalidActionError("The winning bid must be positive")
            if winner.cash < bid:
                raise InvalidActionError("The winner does not have enough cash for that bid")
            self._pay(winner, bid, reason=f"auction for {space.name}")
            if not winner.active:
                raise InvalidActionError("The auction winner failed to pay")
            asset.owner_id = winner.id
            self._log(f"{winner.name} won {space.name} at auction for ${bid}.")

        if auction_phase == GamePhase.AWAITING_AUCTION:
            if self.state.bank_auction_queue:
                next_position = self.state.bank_auction_queue.pop(0)
                self.state.pending_property = next_position
                self.state.phase = GamePhase.AWAITING_AUCTION
                self._log(
                    f"The Bank now auctions {SPACE_BY_POSITION[next_position].name}."
                )
            else:
                self.state.pending_property = None
                if self.state.phase != GamePhase.GAME_OVER:
                    self.state.phase = GamePhase.ACTIONS
        else:
            self.state.pending_property = None
            if self.state.phase != GamePhase.GAME_OVER:
                self.state.phase = GamePhase.ACTIONS
        return space

    def end_turn(self) -> None:
        self._require_phase(GamePhase.ACTIONS)
        if self.state.winner_id is not None:
            self.state.phase = GamePhase.GAME_OVER
            return

        player = self.current_player
        if player.active and self.state.extra_turn and not player.in_jail:
            self.state.extra_turn = False
            self.state.phase = GamePhase.AWAITING_ROLL
            self._log(f"{player.name} earned another roll for doubles.")
            return

        next_index = self._next_active_player_index(self.state.current_player_index)
        self.state.current_player_index = next_index
        self.state.turn_number += 1
        self.state.doubles_in_turn = 0
        self.state.extra_turn = False
        self.state.last_roll = None
        self.state.pending_property = None
        self.state.phase = GamePhase.AWAITING_ROLL
        self._log(f"Turn {self.state.turn_number}: {self.current_player.name}.")

    def calculate_rent(
        self,
        position: int,
        *,
        dice_total: int | None = None,
        rent_multiplier: int = 1,
        utility_multiplier: int | None = None,
    ) -> int:
        space = self._ownable_space(position)
        asset = self.state.properties[position]
        if asset.owner_id is None or asset.mortgaged:
            return 0

        if space.kind == SpaceKind.STREET:
            rent = space.rents[asset.houses]
            if asset.houses == 0 and self.owns_color_group(asset.owner_id, space.group):
                rent *= 2
            return rent * rent_multiplier

        if space.kind == SpaceKind.RAILROAD:
            railroads = sum(
                1
                for owned in self.assets_of(asset.owner_id)
                if SPACE_BY_POSITION[owned].kind == SpaceKind.RAILROAD
            )
            return space.rents[railroads - 1] * rent_multiplier

        if dice_total is None:
            raise InvalidActionError("A dice total is required to calculate utility rent")
        utilities = sum(
            1
            for owned in self.assets_of(asset.owner_id)
            if SPACE_BY_POSITION[owned].kind == SpaceKind.UTILITY
        )
        multiplier = (
            utility_multiplier
            if utility_multiplier is not None
            else (10 if utilities == 2 else 4)
        )
        return dice_total * multiplier

    def owns_color_group(self, player_id: str, group: ColorGroup | None) -> bool:
        if group is None:
            return False
        grouped_spaces = spaces_in_group(group)
        return bool(grouped_spaces) and all(
            self.state.properties[space.position].owner_id == player_id for space in grouped_spaces
        )

    def build(self, player_id: str, position: int) -> None:
        self._require_management_phase()
        player = self.player_by_id(player_id)
        space = self._street_owned_by(position, player_id)
        asset = self.state.properties[position]
        if space.group is None:  # Defensive; _street_owned_by guarantees a street.
            raise InvalidActionError("The street has no color group")
        grouped = spaces_in_group(space.group)
        group_assets = [self.state.properties[item.position] for item in grouped]
        if not self.owns_color_group(player_id, space.group):
            raise InvalidActionError("A complete color group is required to build")
        if any(item.mortgaged for item in group_assets):
            raise InvalidActionError(
                "Nothing can be built while a property in the group is mortgaged"
            )
        if asset.houses >= 5:
            raise InvalidActionError("This property already has a hotel")
        if asset.houses != min(item.houses for item in group_assets):
            raise InvalidActionError("Buildings must be added evenly across the color group")
        if player.cash < space.house_cost:
            raise InvalidActionError("The player does not have enough cash")

        if asset.houses < 4:
            if self.state.bank_houses < 1:
                raise InvalidActionError("The Bank has no houses left")
            self.state.bank_houses -= 1
        else:
            if self.state.bank_hotels < 1:
                raise InvalidActionError("The Bank has no hotels left")
            self.state.bank_hotels -= 1
            self.state.bank_houses += 4
        player.cash -= space.house_cost
        asset.houses += 1
        building = "hotel" if asset.houses == 5 else "house"
        self._log(f"{player.name} built a {building} on {space.name} for ${space.house_cost}.")

    def sell_building(self, player_id: str, position: int) -> None:
        self._require_management_phase()
        player = self.player_by_id(player_id)
        space = self._street_owned_by(position, player_id)
        self._sell_one_building(player, space)

    def mortgage(self, player_id: str, position: int) -> None:
        self._require_management_phase()
        player = self.player_by_id(player_id)
        space = self._property_owned_by(position, player_id)
        self._mortgage_asset(player, space)

    def unmortgage(self, player_id: str, position: int) -> None:
        self._require_management_phase()
        player = self.player_by_id(player_id)
        space = self._property_owned_by(position, player_id)
        asset = self.state.properties[position]
        if not asset.mortgaged:
            raise InvalidActionError("This property is not mortgaged")
        cost = space.mortgage + self._ten_percent_rounded_up(space.mortgage)
        if player.cash < cost:
            raise InvalidActionError(f"Unmortgaging requires ${cost}")
        player.cash -= cost
        asset.mortgaged = False
        self._log(f"{player.name} unmortgaged {space.name} for ${cost}.")

    def execute_trade(self, offer: TradeOffer) -> None:
        self._require_management_phase()
        proposer = self.player_by_id(offer.proposer_id)
        recipient = self.player_by_id(offer.recipient_id)
        if proposer.id == recipient.id:
            raise InvalidActionError("A player cannot trade with themselves")
        if not proposer.active or not recipient.active:
            raise InvalidActionError("Bankrupt players cannot trade")
        if type(offer.offered_cash) is not int or type(offer.requested_cash) is not int:
            raise InvalidActionError("Trade cash amounts must be whole numbers")
        if offer.offered_cash < 0 or offer.requested_cash < 0:
            raise InvalidActionError("Trade cash amounts cannot be negative")
        property_lists = (offer.offered_properties, offer.requested_properties)
        if any(type(position) is not int for positions in property_lists for position in positions):
            raise InvalidActionError("Trade properties must use integer board positions")
        if any(
            not isinstance(deck, DeckKind)
            for cards in (offer.offered_jail_cards, offer.requested_jail_cards)
            for deck in cards
        ):
            raise InvalidActionError("Trade Jail cards must refer to a valid deck")
        if any(type(position) is not int for position in offer.unmortgage_on_transfer):
            raise InvalidActionError("Mortgage payoff positions must be integers")
        if len(set(offer.offered_properties)) != len(offer.offered_properties):
            raise InvalidActionError("A property is repeated in the offer")
        if len(set(offer.requested_properties)) != len(offer.requested_properties):
            raise InvalidActionError("A property is repeated in the request")
        if set(offer.offered_properties) & set(offer.requested_properties):
            raise InvalidActionError("The same property cannot be on both sides of a trade")
        immediate_unmortgages = set(offer.unmortgage_on_transfer)
        if len(immediate_unmortgages) != len(offer.unmortgage_on_transfer):
            raise InvalidActionError("A property is repeated in the immediate payoff list")
        transferred = set(offer.offered_properties) | set(offer.requested_properties)
        if not immediate_unmortgages <= transferred:
            raise InvalidActionError("Only transferred properties can be unmortgaged immediately")

        for position in offer.offered_properties:
            self._validate_transferable(position, proposer.id)
        for position in offer.requested_properties:
            self._validate_transferable(position, recipient.id)
        self._validate_jail_cards(proposer, offer.offered_jail_cards)
        self._validate_jail_cards(recipient, offer.requested_jail_cards)
        if any(
            not self.state.properties[position].mortgaged
            for position in immediate_unmortgages
        ):
            raise InvalidActionError("An immediate payoff property is not mortgaged")

        proposer_interest = self._mortgage_transfer_cost(
            offer.requested_properties,
            immediate_unmortgages,
        )
        recipient_interest = self._mortgage_transfer_cost(
            offer.offered_properties,
            immediate_unmortgages,
        )
        proposer_balance = (
            proposer.cash - offer.offered_cash + offer.requested_cash - proposer_interest
        )
        recipient_balance = (
            recipient.cash - offer.requested_cash + offer.offered_cash - recipient_interest
        )
        if proposer_balance < 0 or recipient_balance < 0:
            raise InvalidActionError("Both players must cover cash and immediate mortgage interest")

        proposer.cash = proposer_balance
        recipient.cash = recipient_balance
        for position in offer.offered_properties:
            self.state.properties[position].owner_id = recipient.id
        for position in offer.requested_properties:
            self.state.properties[position].owner_id = proposer.id
        for position in immediate_unmortgages:
            self.state.properties[position].mortgaged = False
        self._transfer_cards(proposer, recipient, offer.offered_jail_cards)
        self._transfer_cards(recipient, proposer, offer.requested_jail_cards)
        self._log(f"{proposer.name} and {recipient.name} completed a trade.")

    def liquidation_value(self, player_id: str) -> int:
        player = self.player_by_id(player_id)
        total = player.cash
        for position in self.assets_of(player_id):
            space = SPACE_BY_POSITION[position]
            asset = self.state.properties[position]
            total += asset.houses * (space.house_cost // 2)
            if not asset.mortgaged:
                total += space.mortgage
        return total

    def validate_invariants(self) -> None:
        state = self.state
        if not isinstance(state.game_id, str) or not state.game_id:
            raise ValueError("Game ID cannot be empty")
        integer_fields = {
            "current_player_index": state.current_player_index,
            "turn_number": state.turn_number,
            "doubles_in_turn": state.doubles_in_turn,
            "bank_houses": state.bank_houses,
            "bank_hotels": state.bank_hotels,
        }
        if any(type(value) is not int for value in integer_fields.values()):
            raise ValueError("Core game counters must be integers")
        if type(state.extra_turn) is not bool:
            raise ValueError("extra_turn must be a boolean")
        if not isinstance(state.phase, GamePhase):
            raise ValueError("Invalid game phase type")
        if state.pending_property is not None and type(state.pending_property) is not int:
            raise ValueError("Pending property must be an integer or null")
        if not isinstance(state.bank_auction_queue, list) or any(
            type(position) is not int for position in state.bank_auction_queue
        ):
            raise ValueError("Bank auction queue must contain integer positions")
        if state.winner_id is not None and not isinstance(state.winner_id, str):
            raise ValueError("Winner ID must be a string or null")
        if not isinstance(state.history, list) or any(
            not isinstance(message, str) for message in state.history
        ):
            raise ValueError("Game history must contain strings")
        if not isinstance(state.players, list):
            raise ValueError("Players must be stored in a list")
        if not self.rules.min_players <= len(state.players) <= self.rules.max_players:
            raise ValueError("Invalid number of players")
        if any(
            not isinstance(player.id, str) or not isinstance(player.name, str)
            for player in state.players
        ):
            raise ValueError("Player IDs and names must be strings")
        ids = [player.id for player in state.players]
        names = [player.name.casefold() for player in state.players]
        text_values = [state.game_id, *ids, *(player.name for player in state.players), *state.history]
        if any(_contains_terminal_control(value) for value in text_values):
            raise ValueError("Text state cannot contain terminal control characters")
        if any(not player.id or not player.name.strip() for player in state.players):
            raise ValueError("Player IDs and names cannot be empty")
        if len(ids) != len(set(ids)):
            raise ValueError("Player IDs must be unique")
        if len(names) != len(set(names)):
            raise ValueError("Player names must be unique")
        if not 0 <= state.current_player_index < len(state.players):
            raise ValueError("Invalid current player index")
        if state.turn_number < 1:
            raise ValueError("Turn number must be positive")
        if not 0 <= state.doubles_in_turn <= 2:
            raise ValueError("Invalid consecutive doubles count")
        if set(state.properties) != set(OWNABLE_POSITIONS):
            raise ValueError("Property state does not match the classic board")
        for player in state.players:
            if any(
                type(value) is not int
                for value in (player.cash, player.position, player.jail_turns)
            ):
                raise ValueError(f"Player counters must be integers for {player.name}")
            if type(player.in_jail) is not bool or type(player.bankrupt) is not bool:
                raise ValueError(f"Player flags must be booleans for {player.name}")
            if not 0 <= player.position < self.rules.board_size:
                raise ValueError(f"Invalid position for {player.name}")
            if player.cash < 0:
                raise ValueError(f"Negative cash balance for {player.name}")
            if not 0 <= player.jail_turns < self.rules.max_jail_rolls:
                raise ValueError(f"Invalid Jail turn count for {player.name}")
            if player.in_jail and player.position != 10:
                raise ValueError(f"Jailed player {player.name} is not at Jail")
            if not player.in_jail and player.jail_turns != 0:
                raise ValueError(f"Non-jailed player {player.name} has Jail turns")
            if set(player.jail_cards) != set(DeckKind):
                raise ValueError(f"Invalid Jail card counters for {player.name}")
            if any(type(count) is not int for count in player.jail_cards.values()):
                raise ValueError(f"Jail card counters must be integers for {player.name}")
            if any(count < 0 for count in player.jail_cards.values()):
                raise ValueError(f"Negative Jail card count for {player.name}")
            if player.bankrupt:
                if player.cash or player.in_jail or any(player.jail_cards.values()):
                    raise ValueError(f"Bankrupt player {player.name} still has liquid assets")
                if any(asset.owner_id == player.id for asset in state.properties.values()):
                    raise ValueError(f"Bankrupt player {player.name} still owns property")
        for position, asset in state.properties.items():
            if type(position) is not int:
                raise ValueError("Property positions must be integers")
            if not isinstance(asset, PropertyState):
                raise ValueError(f"Invalid property state at position {position}")
            if asset.owner_id is not None and not isinstance(asset.owner_id, str):
                raise ValueError(f"Invalid owner ID at position {position}")
            if type(asset.houses) is not int or type(asset.mortgaged) is not bool:
                raise ValueError(f"Invalid property state types at position {position}")
            if asset.owner_id is not None and asset.owner_id not in ids:
                raise ValueError(f"Unknown owner at position {position}")
            if not 0 <= asset.houses <= 5:
                raise ValueError(f"Invalid building level at position {position}")
            if asset.houses and SPACE_BY_POSITION[position].kind != SpaceKind.STREET:
                raise ValueError(f"Buildings on a non-street at position {position}")
            if asset.houses and asset.mortgaged:
                raise ValueError(f"Mortgaged property has buildings at position {position}")
            if asset.owner_id is None and (asset.houses or asset.mortgaged):
                raise ValueError(f"Unowned property has state at position {position}")

        for group in ColorGroup:
            grouped = spaces_in_group(group)
            group_assets = [state.properties[space.position] for space in grouped]
            levels = [asset.houses for asset in group_assets]
            if max(levels) - min(levels) > 1:
                raise ValueError(f"Buildings are uneven in color group {group.value}")
            if any(levels):
                owners = {asset.owner_id for asset in group_assets}
                if len(owners) != 1 or None in owners:
                    raise ValueError(f"Buildings exist without a monopoly in {group.value}")
                if any(asset.mortgaged for asset in group_assets):
                    raise ValueError(f"A built color group is mortgaged: {group.value}")

        used_houses = sum(
            asset.houses for asset in state.properties.values() if 0 < asset.houses < 5
        )
        used_hotels = sum(asset.houses == 5 for asset in state.properties.values())
        if not 0 <= state.bank_houses <= self.rules.bank_houses:
            raise ValueError("Invalid Bank house inventory")
        if not 0 <= state.bank_hotels <= self.rules.bank_hotels:
            raise ValueError("Invalid Bank hotel inventory")
        if state.bank_houses + used_houses != self.rules.bank_houses:
            raise ValueError("House inventory is inconsistent")
        if state.bank_hotels + used_hotels != self.rules.bank_hotels:
            raise ValueError("Hotel inventory is inconsistent")

        if set(state.decks) != set(DeckKind):
            raise ValueError("Both card decks must exist exactly once")
        expected = Counter(card_id for card_id in CARD_BY_ID)
        actual: Counter[str] = Counter()
        for deck_kind, deck in state.decks.items():
            if not isinstance(deck_kind, DeckKind) or not isinstance(deck.cards, list):
                raise ValueError("Invalid deck state types")
            for card_id in deck.cards:
                if not isinstance(card_id, str):
                    raise ValueError(f"Invalid card ID type in {deck_kind.value}")
                card = CARD_BY_ID.get(card_id)
                if card is None or card.deck != deck_kind:
                    raise ValueError(f"Invalid card {card_id!r} in {deck_kind.value}")
                actual[card_id] += 1
        for player in state.players:
            actual["chance_jail_free"] += player.jail_cards.get(DeckKind.CHANCE, 0)
            actual["chest_jail_free"] += player.jail_cards.get(DeckKind.COMMUNITY_CHEST, 0)
        if actual != expected:
            raise ValueError("Decks and held cards are inconsistent")

        if state.last_roll is not None:
            if not isinstance(state.last_roll, DiceRoll) or any(
                type(value) is not int
                for value in (state.last_roll.first, state.last_roll.second)
            ):
                raise ValueError("Invalid saved dice types")
            if (
                state.last_roll.first not in range(1, 7)
                or state.last_roll.second not in range(1, 7)
            ):
                raise ValueError("Saved dice values must be between 1 and 6")
        if state.extra_turn:
            if (
                state.last_roll is None
                or not state.last_roll.is_double
                or state.doubles_in_turn not in {1, 2}
                or state.phase not in {GamePhase.ACTIONS, GamePhase.AWAITING_PURCHASE}
            ):
                raise ValueError("Extra-turn state is inconsistent with the last roll")
        if state.phase == GamePhase.AWAITING_ROLL and state.doubles_in_turn:
            if state.last_roll is None or not state.last_roll.is_double:
                raise ValueError("A pending doubles roll has no matching previous roll")
        if (
            state.phase == GamePhase.AWAITING_ROLL
            and state.doubles_in_turn == 0
            and state.last_roll is not None
        ):
            raise ValueError("A fresh turn cannot retain a previous roll")
        if state.phase in {GamePhase.AWAITING_PURCHASE, GamePhase.AWAITING_AUCTION}:
            pending = state.pending_property
            if pending not in state.properties:
                raise ValueError("Property decision has no valid pending property")
            if state.properties[pending].owner_id is not None:
                raise ValueError("Pending property is already owned")
            if state.phase == GamePhase.AWAITING_PURCHASE and (
                state.current_player.position != pending or not state.current_player.active
            ):
                raise ValueError("Pending purchase does not match the current player")
        elif state.pending_property is not None:
            raise ValueError("A property is pending outside the purchase phase")
        if state.phase == GamePhase.AWAITING_AUCTION:
            auctioned = [state.pending_property, *state.bank_auction_queue]
            if len(auctioned) != len(set(auctioned)):
                raise ValueError("A property is duplicated in the Bank auction queue")
            if any(
                position not in state.properties
                or state.properties[position].owner_id is not None
                for position in state.bank_auction_queue
            ):
                raise ValueError("Bank auction queue contains an unavailable property")
        elif state.bank_auction_queue:
            raise ValueError("Bank auction queue exists outside the auction phase")

        active = [player for player in state.players if player.active]
        if len(active) == 1:
            if state.phase != GamePhase.GAME_OVER or state.winner_id != active[0].id:
                raise ValueError("A finished game must identify its sole active winner")
        elif len(active) < 1:
            raise ValueError("At least one player must remain active")
        elif state.phase == GamePhase.GAME_OVER or state.winner_id is not None:
            raise ValueError("A game with multiple active players cannot be over")
        if (
            state.phase
            not in {GamePhase.ACTIONS, GamePhase.AWAITING_AUCTION, GamePhase.GAME_OVER}
            and not state.current_player.active
        ):
            raise ValueError("An inactive player cannot await a decision")
        if state.extra_turn and state.current_player.in_jail:
            raise ValueError("A jailed player cannot have an extra turn")

    def _resolve_jail_roll(self, player: Player, roll: DiceRoll) -> None:
        self.state.extra_turn = False
        self.state.doubles_in_turn = 0
        if roll.is_double:
            player.in_jail = False
            player.jail_turns = 0
            self._log(f"{player.name} rolled doubles and left Jail.")
            self._move_by(player, roll.total)
            self._resolve_space(player, dice_total=roll.total)
            return

        player.jail_turns += 1
        if player.jail_turns < self.rules.max_jail_rolls:
            self._log(
                f"{player.name} remains in Jail "
                f"({player.jail_turns}/{self.rules.max_jail_rolls} failed rolls)."
            )
            self.state.phase = GamePhase.ACTIONS
            return

        self._log(f"{player.name} failed the third Jail roll and must pay the fee.")
        self._pay(player, self.rules.jail_fee, reason="mandatory Jail fee")
        if player.active:
            player.in_jail = False
            player.jail_turns = 0
            self._move_by(player, roll.total)
            self._resolve_space(player, dice_total=roll.total)
        elif self.state.phase not in {GamePhase.GAME_OVER, GamePhase.AWAITING_AUCTION}:
            self.state.phase = GamePhase.ACTIONS

    def _move_by(self, player: Player, steps: int) -> None:
        old_position = player.position
        destination = (old_position + steps) % self.rules.board_size
        if steps > 0 and old_position + steps >= self.rules.board_size:
            self._collect_go(player)
        player.position = destination
        self._log(f"{player.name} moved to {SPACE_BY_POSITION[destination].name}.")

    def _move_to(self, player: Player, destination: int) -> None:
        if destination < player.position or destination == 0:
            self._collect_go(player)
        player.position = destination
        self._log(f"{player.name} moved to {SPACE_BY_POSITION[destination].name}.")

    def _collect_go(self, player: Player) -> None:
        player.cash += self.rules.go_salary
        self._log(f"{player.name} collected ${self.rules.go_salary} for passing GO.")

    def _resolve_space(
        self,
        player: Player,
        *,
        dice_total: int | None = None,
        rent_multiplier: int = 1,
        utility_multiplier: int | None = None,
    ) -> None:
        if self.state.phase == GamePhase.GAME_OVER or not player.active:
            return
        self.state.pending_property = None
        self.state.phase = GamePhase.ACTIONS
        space = SPACE_BY_POSITION[player.position]

        if space.is_ownable:
            asset = self.state.properties[space.position]
            if asset.owner_id is None:
                self.state.pending_property = space.position
                self.state.phase = GamePhase.AWAITING_PURCHASE
                self._log(f"{space.name} is available for ${space.price}.")
            elif asset.owner_id != player.id and not asset.mortgaged:
                owner = self.player_by_id(asset.owner_id)
                rent = self.calculate_rent(
                    space.position,
                    dice_total=dice_total,
                    rent_multiplier=rent_multiplier,
                    utility_multiplier=utility_multiplier,
                )
                self._pay(player, rent, creditor_id=owner.id, reason=f"rent for {space.name}")
            return

        if space.kind == SpaceKind.TAX:
            self._pay(player, space.tax, reason=space.name)
        elif space.kind == SpaceKind.CHANCE:
            self._draw_card(player, DeckKind.CHANCE)
        elif space.kind == SpaceKind.COMMUNITY_CHEST:
            self._draw_card(player, DeckKind.COMMUNITY_CHEST)
        elif space.kind == SpaceKind.GO_TO_JAIL:
            self._send_to_jail(player)

    def _draw_card(self, player: Player, deck_kind: DeckKind) -> None:
        deck = self.state.decks[deck_kind]
        card_id = deck.cards.pop(0)
        card = CARD_BY_ID[card_id]
        self._log(f"{player.name} drew: {card.text}")
        if card.action != CardAction.GET_OUT_OF_JAIL:
            deck.cards.append(card_id)
        self._apply_card(player, card)

    def _apply_card(self, player: Player, card: Card) -> None:
        action = card.action
        if action == CardAction.CASH:
            if card.value >= 0:
                player.cash += card.value
                self._log(f"{player.name} received ${card.value} from the Bank.")
            else:
                self._pay(player, -card.value, reason=card.text)
        elif action == CardAction.MOVE_TO:
            self._move_to(player, card.value)
            self._resolve_space(player, dice_total=self._last_dice_total())
        elif action == CardAction.MOVE_RELATIVE:
            self._move_by(player, card.value)
            self._resolve_space(player, dice_total=self._last_dice_total())
        elif action == CardAction.GO_TO_JAIL:
            self._send_to_jail(player)
        elif action == CardAction.GET_OUT_OF_JAIL:
            player.jail_cards[card.deck] += 1
            self._log(f"{player.name} kept a Get Out of Jail Free card.")
        elif action == CardAction.CASH_FROM_EACH:
            for other in self._other_active_players(player.id):
                if not player.active or self.state.phase == GamePhase.GAME_OVER:
                    break
                self._pay(other, card.value, creditor_id=player.id, reason=card.text)
        elif action == CardAction.CASH_TO_EACH:
            for other in self._other_active_players(player.id):
                if not player.active:
                    break
                self._pay(player, card.value, creditor_id=other.id, reason=card.text)
        elif action == CardAction.REPAIRS:
            houses, hotels = self._building_counts(player.id)
            amount = houses * card.value + hotels * card.secondary_value
            self._pay(player, amount, reason=card.text)
        elif action == CardAction.NEAREST_RAILROAD:
            destination = self._nearest_position(player.position, SpaceKind.RAILROAD)
            self._move_to(player, destination)
            self._resolve_space(
                player,
                dice_total=self._last_dice_total(),
                rent_multiplier=2,
            )
        elif action == CardAction.NEAREST_UTILITY:
            destination = self._nearest_position(player.position, SpaceKind.UTILITY)
            self._move_to(player, destination)
            utility = self.state.properties[destination]
            if utility.owner_id not in {None, player.id} and not utility.mortgaged:
                utility_roll = DiceRoll(self.rng.randint(1, 6), self.rng.randint(1, 6))
                self._log(
                    f"Utility rent roll: {utility_roll.first} + {utility_roll.second} "
                    f"= {utility_roll.total}."
                )
                self._resolve_space(
                    player,
                    dice_total=utility_roll.total,
                    utility_multiplier=10,
                )
            else:
                self._resolve_space(player, dice_total=self._last_dice_total())

    def _send_to_jail(self, player: Player) -> None:
        player.position = 10
        player.in_jail = True
        player.jail_turns = 0
        self.state.extra_turn = False
        self.state.doubles_in_turn = 0
        self.state.pending_property = None
        self.state.phase = GamePhase.ACTIONS
        self._log(f"{player.name} is in Jail.")

    def _pay(
        self,
        payer: Player,
        amount: int,
        *,
        creditor_id: str | None = None,
        reason: str,
    ) -> None:
        if amount < 0:
            raise ValueError("A payment cannot be negative")
        if amount == 0 or not payer.active:
            return
        if payer.cash < amount:
            self._raise_cash_automatically(payer, amount)

        paid = min(payer.cash, amount)
        payer.cash -= paid
        creditor = self.player_by_id(creditor_id) if creditor_id else None
        if creditor is not None and creditor.active:
            creditor.cash += paid
        self._log(f"{payer.name} paid ${paid} for {reason}.")
        if paid < amount:
            self._bankrupt(payer, creditor)

    def _raise_cash_automatically(self, player: Player, target: int) -> None:
        """Deterministically liquidate legal assets before bankruptcy.

        Human players can manage assets voluntarily. During an unavoidable
        payment the console engine sells buildings evenly, then mortgages
        eligible deeds so a player is never declared bankrupt while legal
        liquidity remains.
        """

        while player.cash < target:
            candidates = [
                SPACE_BY_POSITION[position]
                for position in self.assets_of(player.id)
                if self._can_sell_one_building(position)
            ]
            if not candidates:
                break
            candidates.sort(key=lambda item: (item.house_cost, item.position), reverse=True)
            self._sell_one_building(player, candidates[0], automatic=True)

        for position in self.assets_of(player.id):
            if player.cash >= target:
                break
            space = SPACE_BY_POSITION[position]
            if self._can_mortgage(position):
                self._mortgage_asset(player, space, automatic=True)

    def _bankrupt(self, player: Player, creditor: Player | None) -> None:
        self._log(f"{player.name} is bankrupt.")
        player.bankrupt = True
        player.in_jail = False
        player.jail_turns = 0
        if player.id == self.current_player.id:
            self.state.extra_turn = False

        transferred_mortgages: list[int] = []
        bank_auction_positions: list[int] = []
        for position in self.assets_of(player.id):
            asset = self.state.properties[position]
            self._return_buildings_to_bank(asset)
            if creditor is not None and creditor.active:
                asset.owner_id = creditor.id
                if asset.mortgaged:
                    transferred_mortgages.append(position)
            else:
                asset.owner_id = None
                asset.mortgaged = False
                bank_auction_positions.append(position)

        for deck_kind in DeckKind:
            count = player.jail_cards[deck_kind]
            if creditor is not None and creditor.active:
                creditor.jail_cards[deck_kind] += count
            else:
                card_id = (
                    "chance_jail_free"
                    if deck_kind == DeckKind.CHANCE
                    else "chest_jail_free"
                )
                self.state.decks[deck_kind].cards.extend([card_id] * count)
            player.jail_cards[deck_kind] = 0

        if creditor is not None and creditor.active and transferred_mortgages:
            self._check_winner()
            if self.state.phase == GamePhase.GAME_OVER:
                return
            interest = self._mortgage_interest(transferred_mortgages)
            self._pay(creditor, interest, reason="interest on inherited mortgages")
        self._check_winner()
        if (
            creditor is None
            and bank_auction_positions
            and self.state.phase != GamePhase.GAME_OVER
        ):
            self._start_bank_auctions(bank_auction_positions)

    def _start_bank_auctions(self, positions: list[int]) -> None:
        ordered = sorted(positions)
        if self.state.phase == GamePhase.AWAITING_AUCTION:
            self.state.bank_auction_queue.extend(ordered)
            return
        self.state.pending_property = ordered[0]
        self.state.bank_auction_queue = ordered[1:]
        self.state.phase = GamePhase.AWAITING_AUCTION
        self._log(f"The Bank auctions {SPACE_BY_POSITION[ordered[0]].name}.")

    def _return_buildings_to_bank(self, asset: PropertyState) -> None:
        if asset.houses == 5:
            self.state.bank_hotels += 1
        elif asset.houses:
            self.state.bank_houses += asset.houses
        asset.houses = 0

    def _sell_one_building(
        self,
        player: Player,
        space: Space,
        *,
        automatic: bool = False,
    ) -> None:
        asset = self.state.properties[space.position]
        if not self._can_sell_one_building(space.position):
            raise InvalidActionError("Buildings must be sold evenly across the color group")
        if asset.houses == 5:
            if self.state.bank_houses < 4:
                raise InvalidActionError("The Bank lacks four houses to break up the hotel")
            self.state.bank_houses -= 4
            self.state.bank_hotels += 1
        else:
            self.state.bank_houses += 1
        asset.houses -= 1
        proceeds = space.house_cost // 2
        player.cash += proceeds
        prefix = "Automatically: " if automatic else ""
        self._log(f"{prefix}{player.name} sold one building on {space.name} for ${proceeds}.")

    def _mortgage_asset(
        self,
        player: Player,
        space: Space,
        *,
        automatic: bool = False,
    ) -> None:
        if not self._can_mortgage(space.position):
            raise InvalidActionError(
                "The property cannot be mortgaged while its group has buildings"
            )
        asset = self.state.properties[space.position]
        asset.mortgaged = True
        player.cash += space.mortgage
        prefix = "Automatically: " if automatic else ""
        self._log(f"{prefix}{player.name} mortgaged {space.name} for ${space.mortgage}.")

    def _can_sell_one_building(self, position: int) -> bool:
        space = SPACE_BY_POSITION[position]
        asset = self.state.properties[position]
        if space.kind != SpaceKind.STREET or asset.houses < 1 or space.group is None:
            return False
        levels = [
            self.state.properties[item.position].houses for item in spaces_in_group(space.group)
        ]
        if asset.houses != max(levels):
            return False
        return asset.houses != 5 or self.state.bank_houses >= 4

    def _can_mortgage(self, position: int) -> bool:
        space = SPACE_BY_POSITION[position]
        asset = self.state.properties[position]
        if asset.owner_id is None or asset.mortgaged or asset.houses:
            return False
        if space.kind == SpaceKind.STREET and space.group is not None:
            return all(
                self.state.properties[item.position].houses == 0
                for item in spaces_in_group(space.group)
            )
        return True

    def _check_winner(self) -> None:
        if self.state.phase == GamePhase.GAME_OVER and self.state.winner_id is not None:
            return
        active = [player for player in self.state.players if player.active]
        if len(active) == 1:
            winner = active[0]
            self.state.winner_id = winner.id
            self.state.phase = GamePhase.GAME_OVER
            self.state.extra_turn = False
            self._log(f"{winner.name} wins the game!")

    def _next_active_player_index(self, current: int) -> int:
        for offset in range(1, len(self.state.players) + 1):
            candidate = (current + offset) % len(self.state.players)
            if self.state.players[candidate].active:
                return candidate
        raise InvalidActionError("There is no active player")

    def _nearest_position(self, current: int, kind: SpaceKind) -> int:
        candidates = [space.position for space in BOARD if space.kind == kind]
        return min(
            candidates,
            key=lambda position: (position - current) % self.rules.board_size or 40,
        )

    def _other_active_players(self, player_id: str) -> list[Player]:
        return [
            player for player in self.state.players if player.id != player_id and player.active
        ]

    def _building_counts(self, player_id: str) -> tuple[int, int]:
        houses = 0
        hotels = 0
        for position in self.assets_of(player_id):
            level = self.state.properties[position].houses
            if level == 5:
                hotels += 1
            else:
                houses += level
        return houses, hotels

    def _validate_transferable(self, position: int, owner_id: str) -> None:
        space = self._property_owned_by(position, owner_id)
        asset = self.state.properties[position]
        if asset.houses:
            raise InvalidActionError("Buildings cannot be traded")
        if space.kind == SpaceKind.STREET and space.group is not None:
            if any(
                self.state.properties[item.position].houses
                for item in spaces_in_group(space.group)
            ):
                raise InvalidActionError("Sell all buildings in the color group before trading")

    def _validate_jail_cards(self, player: Player, cards: tuple[DeckKind, ...]) -> None:
        requested = Counter(cards)
        for deck_kind, count in requested.items():
            if player.jail_cards.get(deck_kind, 0) < count:
                raise InvalidActionError("A player cannot trade a Jail card they do not own")

    def _transfer_cards(
        self,
        source: Player,
        destination: Player,
        cards: tuple[DeckKind, ...],
    ) -> None:
        for deck_kind in cards:
            source.jail_cards[deck_kind] -= 1
            destination.jail_cards[deck_kind] += 1

    def _mortgage_interest(self, positions: Iterable[int]) -> int:
        return sum(
            self._ten_percent_rounded_up(SPACE_BY_POSITION[position].mortgage)
            for position in positions
            if self.state.properties[position].mortgaged
        )

    def _mortgage_transfer_cost(
        self,
        positions: Iterable[int],
        immediate_unmortgages: set[int],
    ) -> int:
        cost = 0
        for position in positions:
            if not self.state.properties[position].mortgaged:
                continue
            mortgage = SPACE_BY_POSITION[position].mortgage
            cost += self._ten_percent_rounded_up(mortgage)
            if position in immediate_unmortgages:
                cost += mortgage
        return cost

    @staticmethod
    def _ten_percent_rounded_up(amount: int) -> int:
        return (amount + 9) // 10

    def _last_dice_total(self) -> int:
        return self.state.last_roll.total if self.state.last_roll else 0

    def _pending_position(self) -> int:
        if self.state.pending_property is None:
            raise InvalidActionError("There is no pending property decision")
        return self.state.pending_property

    def _property_owned_by(self, position: int, player_id: str) -> Space:
        space = self._ownable_space(position)
        if self.state.properties[position].owner_id != player_id:
            raise InvalidActionError("The player does not own this property")
        return space

    def _street_owned_by(self, position: int, player_id: str) -> Space:
        space = self._property_owned_by(position, player_id)
        if space.kind != SpaceKind.STREET:
            raise InvalidActionError("Only streets can contain buildings")
        return space

    def _ownable_space(self, position: int) -> Space:
        space = SPACE_BY_POSITION.get(position)
        if space is None or not space.is_ownable:
            raise InvalidActionError("That board position is not ownable")
        return space

    def _require_phase(self, *phases: GamePhase) -> None:
        if self.state.phase not in phases:
            expected = ", ".join(phase.value for phase in phases)
            raise InvalidActionError(
                f"Action not allowed during {self.state.phase.value}; expected {expected}"
            )

    def _require_management_phase(self) -> None:
        self._require_phase(
            GamePhase.AWAITING_ROLL,
            GamePhase.AWAITING_PURCHASE,
            GamePhase.ACTIONS,
        )

    def _log(self, message: str) -> None:
        self.state.history.append(message)
        self.revision += 1
        if len(self.state.history) > 2_000:
            del self.state.history[: len(self.state.history) - 2_000]


def _contains_terminal_control(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
