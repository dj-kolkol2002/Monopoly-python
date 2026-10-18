from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from monopoly.models import DeckKind


class CardAction(StrEnum):
    MOVE_TO = "move_to"
    MOVE_RELATIVE = "move_relative"
    CASH = "cash"
    CASH_FROM_EACH = "cash_from_each"
    CASH_TO_EACH = "cash_to_each"
    GO_TO_JAIL = "go_to_jail"
    GET_OUT_OF_JAIL = "get_out_of_jail"
    REPAIRS = "repairs"
    NEAREST_RAILROAD = "nearest_railroad"
    NEAREST_UTILITY = "nearest_utility"


@dataclass(frozen=True, slots=True)
class Card:
    id: str
    deck: DeckKind
    text: str
    action: CardAction
    value: int = 0
    secondary_value: int = 0


CHANCE_CARDS: tuple[Card, ...] = (
    Card("chance_go", DeckKind.CHANCE, "Advance to GO.", CardAction.MOVE_TO, 0),
    Card(
        "chance_illinois",
        DeckKind.CHANCE,
        "Advance to Illinois Avenue.",
        CardAction.MOVE_TO,
        24,
    ),
    Card(
        "chance_st_charles",
        DeckKind.CHANCE,
        "Advance to St. Charles Place.",
        CardAction.MOVE_TO,
        11,
    ),
    Card(
        "chance_utility",
        DeckKind.CHANCE,
        "Advance to the nearest Utility.",
        CardAction.NEAREST_UTILITY,
    ),
    Card(
        "chance_railroad_1",
        DeckKind.CHANCE,
        "Advance to the nearest Railroad and pay double rent if owned.",
        CardAction.NEAREST_RAILROAD,
    ),
    Card(
        "chance_railroad_2",
        DeckKind.CHANCE,
        "Advance to the nearest Railroad and pay double rent if owned.",
        CardAction.NEAREST_RAILROAD,
    ),
    Card(
        "chance_dividend",
        DeckKind.CHANCE,
        "Bank pays you dividend of $50.",
        CardAction.CASH,
        50,
    ),
    Card(
        "chance_jail_free",
        DeckKind.CHANCE,
        "Get Out of Jail Free.",
        CardAction.GET_OUT_OF_JAIL,
    ),
    Card("chance_back_3", DeckKind.CHANCE, "Go back three spaces.", CardAction.MOVE_RELATIVE, -3),
    Card("chance_jail", DeckKind.CHANCE, "Go directly to Jail.", CardAction.GO_TO_JAIL),
    Card(
        "chance_repairs",
        DeckKind.CHANCE,
        "General repairs: pay $25 per house and $100 per hotel.",
        CardAction.REPAIRS,
        25,
        100,
    ),
    Card(
        "chance_speeding",
        DeckKind.CHANCE,
        "Speeding fine: pay $15.",
        CardAction.CASH,
        -15,
    ),
    Card(
        "chance_reading",
        DeckKind.CHANCE,
        "Take a trip to Reading Railroad.",
        CardAction.MOVE_TO,
        5,
    ),
    Card(
        "chance_boardwalk",
        DeckKind.CHANCE,
        "Take a walk on the Boardwalk.",
        CardAction.MOVE_TO,
        39,
    ),
    Card(
        "chance_chairman",
        DeckKind.CHANCE,
        "You have been elected Chairman. Pay each player $50.",
        CardAction.CASH_TO_EACH,
        50,
    ),
    Card(
        "chance_loan",
        DeckKind.CHANCE,
        "Your building loan matures. Collect $150.",
        CardAction.CASH,
        150,
    ),
)

COMMUNITY_CHEST_CARDS: tuple[Card, ...] = (
    Card("chest_go", DeckKind.COMMUNITY_CHEST, "Advance to GO.", CardAction.MOVE_TO, 0),
    Card(
        "chest_bank_error",
        DeckKind.COMMUNITY_CHEST,
        "Bank error in your favor. Collect $200.",
        CardAction.CASH,
        200,
    ),
    Card(
        "chest_doctor",
        DeckKind.COMMUNITY_CHEST,
        "Doctor's fee. Pay $50.",
        CardAction.CASH,
        -50,
    ),
    Card(
        "chest_stock",
        DeckKind.COMMUNITY_CHEST,
        "From sale of stock you get $50.",
        CardAction.CASH,
        50,
    ),
    Card(
        "chest_jail_free",
        DeckKind.COMMUNITY_CHEST,
        "Get Out of Jail Free.",
        CardAction.GET_OUT_OF_JAIL,
    ),
    Card(
        "chest_jail",
        DeckKind.COMMUNITY_CHEST,
        "Go directly to Jail.",
        CardAction.GO_TO_JAIL,
    ),
    Card(
        "chest_holiday",
        DeckKind.COMMUNITY_CHEST,
        "Holiday fund matures. Collect $100.",
        CardAction.CASH,
        100,
    ),
    Card(
        "chest_refund",
        DeckKind.COMMUNITY_CHEST,
        "Income tax refund. Collect $20.",
        CardAction.CASH,
        20,
    ),
    Card(
        "chest_birthday",
        DeckKind.COMMUNITY_CHEST,
        "It is your birthday. Collect $10 from every player.",
        CardAction.CASH_FROM_EACH,
        10,
    ),
    Card(
        "chest_insurance",
        DeckKind.COMMUNITY_CHEST,
        "Life insurance matures. Collect $100.",
        CardAction.CASH,
        100,
    ),
    Card(
        "chest_hospital",
        DeckKind.COMMUNITY_CHEST,
        "Hospital fees. Pay $100.",
        CardAction.CASH,
        -100,
    ),
    Card(
        "chest_school",
        DeckKind.COMMUNITY_CHEST,
        "School fees. Pay $50.",
        CardAction.CASH,
        -50,
    ),
    Card(
        "chest_consultancy",
        DeckKind.COMMUNITY_CHEST,
        "Receive $25 consultancy fee.",
        CardAction.CASH,
        25,
    ),
    Card(
        "chest_repairs",
        DeckKind.COMMUNITY_CHEST,
        "Street repairs: pay $40 per house and $115 per hotel.",
        CardAction.REPAIRS,
        40,
        115,
    ),
    Card(
        "chest_beauty",
        DeckKind.COMMUNITY_CHEST,
        "You have won second prize in a beauty contest. Collect $10.",
        CardAction.CASH,
        10,
    ),
    Card(
        "chest_inherit",
        DeckKind.COMMUNITY_CHEST,
        "You inherit $100.",
        CardAction.CASH,
        100,
    ),
)

ALL_CARDS: tuple[Card, ...] = CHANCE_CARDS + COMMUNITY_CHEST_CARDS
CARD_BY_ID: dict[str, Card] = {card.id: card for card in ALL_CARDS}


def card_ids(deck: DeckKind) -> list[str]:
    cards = CHANCE_CARDS if deck == DeckKind.CHANCE else COMMUNITY_CHEST_CARDS
    return [card.id for card in cards]
