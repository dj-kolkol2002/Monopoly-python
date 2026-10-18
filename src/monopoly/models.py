from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MonopolyError(Exception):
    """Base class for errors that can safely be shown to a player."""


class InvalidActionError(MonopolyError):
    """Raised when an action is not legal in the current state."""


class InvalidSaveError(MonopolyError):
    """Raised when a save file is corrupt or incompatible."""


class SpaceKind(StrEnum):
    GO = "go"
    STREET = "street"
    RAILROAD = "railroad"
    UTILITY = "utility"
    TAX = "tax"
    CHANCE = "chance"
    COMMUNITY_CHEST = "community_chest"
    JAIL = "jail"
    FREE_PARKING = "free_parking"
    GO_TO_JAIL = "go_to_jail"


class ColorGroup(StrEnum):
    BROWN = "brown"
    LIGHT_BLUE = "light_blue"
    PINK = "pink"
    ORANGE = "orange"
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    DARK_BLUE = "dark_blue"


class DeckKind(StrEnum):
    CHANCE = "chance"
    COMMUNITY_CHEST = "community_chest"


class GamePhase(StrEnum):
    AWAITING_ROLL = "awaiting_roll"
    AWAITING_PURCHASE = "awaiting_purchase"
    AWAITING_AUCTION = "awaiting_auction"
    ACTIONS = "actions"
    GAME_OVER = "game_over"


@dataclass(frozen=True, slots=True)
class Space:
    position: int
    name: str
    kind: SpaceKind
    price: int = 0
    mortgage: int = 0
    house_cost: int = 0
    rents: tuple[int, ...] = ()
    group: ColorGroup | None = None
    tax: int = 0

    @property
    def is_ownable(self) -> bool:
        return self.kind in {SpaceKind.STREET, SpaceKind.RAILROAD, SpaceKind.UTILITY}


@dataclass(slots=True)
class PropertyState:
    owner_id: str | None = None
    houses: int = 0
    mortgaged: bool = False


@dataclass(slots=True)
class Player:
    id: str
    name: str
    cash: int = 1_500
    position: int = 0
    in_jail: bool = False
    jail_turns: int = 0
    jail_cards: dict[DeckKind, int] = field(
        default_factory=lambda: {DeckKind.CHANCE: 0, DeckKind.COMMUNITY_CHEST: 0}
    )
    bankrupt: bool = False

    @property
    def active(self) -> bool:
        return not self.bankrupt


@dataclass(frozen=True, slots=True)
class DiceRoll:
    first: int
    second: int

    @property
    def total(self) -> int:
        return self.first + self.second

    @property
    def is_double(self) -> bool:
        return self.first == self.second


@dataclass(slots=True)
class DeckState:
    cards: list[str]


@dataclass(slots=True)
class GameState:
    game_id: str
    players: list[Player]
    properties: dict[int, PropertyState]
    decks: dict[DeckKind, DeckState]
    current_player_index: int = 0
    phase: GamePhase = GamePhase.AWAITING_ROLL
    turn_number: int = 1
    doubles_in_turn: int = 0
    extra_turn: bool = False
    last_roll: DiceRoll | None = None
    pending_property: int | None = None
    bank_auction_queue: list[int] = field(default_factory=list)
    bank_houses: int = 32
    bank_hotels: int = 12
    winner_id: str | None = None
    history: list[str] = field(default_factory=list)

    @property
    def current_player(self) -> Player:
        return self.players[self.current_player_index]


@dataclass(frozen=True, slots=True)
class TradeOffer:
    proposer_id: str
    recipient_id: str
    offered_cash: int = 0
    requested_cash: int = 0
    offered_properties: tuple[int, ...] = ()
    requested_properties: tuple[int, ...] = ()
    offered_jail_cards: tuple[DeckKind, ...] = ()
    requested_jail_cards: tuple[DeckKind, ...] = ()
    unmortgage_on_transfer: tuple[int, ...] = ()


def enum_dict_to_json(values: dict[DeckKind, int]) -> dict[str, int]:
    return {key.value: value for key, value in values.items()}


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, Any]
