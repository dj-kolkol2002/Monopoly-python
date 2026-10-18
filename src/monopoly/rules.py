from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClassicRules:
    """Versioned constants for the classic US ruleset (without house rules)."""

    name: str = "us-classic-2008-2021"
    version: int = 1
    starting_cash: int = 1_500
    go_salary: int = 200
    jail_fee: int = 50
    max_jail_rolls: int = 3
    max_players: int = 4
    min_players: int = 2
    bank_houses: int = 32
    bank_hotels: int = 12
    board_size: int = 40


CLASSIC_RULES = ClassicRules()
