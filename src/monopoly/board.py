from __future__ import annotations

from collections.abc import Iterable

from monopoly.models import ColorGroup, Space, SpaceKind


def _street(
    position: int,
    name: str,
    price: int,
    group: ColorGroup,
    house_cost: int,
    rents: tuple[int, int, int, int, int, int],
) -> Space:
    return Space(
        position=position,
        name=name,
        kind=SpaceKind.STREET,
        price=price,
        mortgage=price // 2,
        house_cost=house_cost,
        rents=rents,
        group=group,
    )


def _railroad(position: int, name: str) -> Space:
    return Space(
        position=position,
        name=name,
        kind=SpaceKind.RAILROAD,
        price=200,
        mortgage=100,
        rents=(25, 50, 100, 200),
    )


def _utility(position: int, name: str) -> Space:
    return Space(position, name, SpaceKind.UTILITY, price=150, mortgage=75)


# Classic Atlantic City board. Static tile definitions are deliberately separate
# from ownership/building state so snapshots stay small and migration-friendly.
BOARD: tuple[Space, ...] = (
    Space(0, "GO", SpaceKind.GO),
    _street(1, "Mediterranean Avenue", 60, ColorGroup.BROWN, 50, (2, 10, 30, 90, 160, 250)),
    Space(2, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(3, "Baltic Avenue", 60, ColorGroup.BROWN, 50, (4, 20, 60, 180, 320, 450)),
    Space(4, "Income Tax", SpaceKind.TAX, tax=200),
    _railroad(5, "Reading Railroad"),
    _street(6, "Oriental Avenue", 100, ColorGroup.LIGHT_BLUE, 50, (6, 30, 90, 270, 400, 550)),
    Space(7, "Chance", SpaceKind.CHANCE),
    _street(8, "Vermont Avenue", 100, ColorGroup.LIGHT_BLUE, 50, (6, 30, 90, 270, 400, 550)),
    _street(9, "Connecticut Avenue", 120, ColorGroup.LIGHT_BLUE, 50, (8, 40, 100, 300, 450, 600)),
    Space(10, "Jail / Just Visiting", SpaceKind.JAIL),
    _street(11, "St. Charles Place", 140, ColorGroup.PINK, 100, (10, 50, 150, 450, 625, 750)),
    _utility(12, "Electric Company"),
    _street(13, "States Avenue", 140, ColorGroup.PINK, 100, (10, 50, 150, 450, 625, 750)),
    _street(14, "Virginia Avenue", 160, ColorGroup.PINK, 100, (12, 60, 180, 500, 700, 900)),
    _railroad(15, "Pennsylvania Railroad"),
    _street(16, "St. James Place", 180, ColorGroup.ORANGE, 100, (14, 70, 200, 550, 750, 950)),
    Space(17, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(18, "Tennessee Avenue", 180, ColorGroup.ORANGE, 100, (14, 70, 200, 550, 750, 950)),
    _street(19, "New York Avenue", 200, ColorGroup.ORANGE, 100, (16, 80, 220, 600, 800, 1_000)),
    Space(20, "Free Parking", SpaceKind.FREE_PARKING),
    _street(21, "Kentucky Avenue", 220, ColorGroup.RED, 150, (18, 90, 250, 700, 875, 1_050)),
    Space(22, "Chance", SpaceKind.CHANCE),
    _street(23, "Indiana Avenue", 220, ColorGroup.RED, 150, (18, 90, 250, 700, 875, 1_050)),
    _street(24, "Illinois Avenue", 240, ColorGroup.RED, 150, (20, 100, 300, 750, 925, 1_100)),
    _railroad(25, "B. & O. Railroad"),
    _street(26, "Atlantic Avenue", 260, ColorGroup.YELLOW, 150, (22, 110, 330, 800, 975, 1_150)),
    _street(27, "Ventnor Avenue", 260, ColorGroup.YELLOW, 150, (22, 110, 330, 800, 975, 1_150)),
    _utility(28, "Water Works"),
    _street(29, "Marvin Gardens", 280, ColorGroup.YELLOW, 150, (24, 120, 360, 850, 1_025, 1_200)),
    Space(30, "Go To Jail", SpaceKind.GO_TO_JAIL),
    _street(31, "Pacific Avenue", 300, ColorGroup.GREEN, 200, (26, 130, 390, 900, 1_100, 1_275)),
    _street(
        32,
        "North Carolina Avenue",
        300,
        ColorGroup.GREEN,
        200,
        (26, 130, 390, 900, 1_100, 1_275),
    ),
    Space(33, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(
        34,
        "Pennsylvania Avenue",
        320,
        ColorGroup.GREEN,
        200,
        (28, 150, 450, 1_000, 1_200, 1_400),
    ),
    _railroad(35, "Short Line"),
    Space(36, "Chance", SpaceKind.CHANCE),
    _street(37, "Park Place", 350, ColorGroup.DARK_BLUE, 200, (35, 175, 500, 1_100, 1_300, 1_500)),
    Space(38, "Luxury Tax", SpaceKind.TAX, tax=100),
    _street(39, "Boardwalk", 400, ColorGroup.DARK_BLUE, 200, (50, 200, 600, 1_400, 1_700, 2_000)),
)

SPACE_BY_POSITION: dict[int, Space] = {space.position: space for space in BOARD}
OWNABLE_POSITIONS: tuple[int, ...] = tuple(space.position for space in BOARD if space.is_ownable)


def spaces_in_group(group: ColorGroup) -> tuple[Space, ...]:
    return tuple(space for space in BOARD if space.group == group)


def owned_positions(properties: dict[int, object], owner_id: str) -> Iterable[int]:
    for position, state in properties.items():
        if getattr(state, "owner_id", None) == owner_id:
            yield position


def validate_board() -> None:
    if len(BOARD) != 40 or {space.position for space in BOARD} != set(range(40)):
        raise RuntimeError("The classic board must contain exactly positions 0 through 39")
    for space in BOARD:
        if space.kind == SpaceKind.STREET and (len(space.rents) != 6 or space.group is None):
            raise RuntimeError(f"Invalid street definition at position {space.position}")


validate_board()
