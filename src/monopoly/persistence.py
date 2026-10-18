from __future__ import annotations

import json
import math
import os
import random
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from monopoly.engine import MonopolyGame
from monopoly.models import (
    DeckKind,
    DeckState,
    DiceRoll,
    GamePhase,
    GameState,
    InvalidSaveError,
    Player,
    PropertyState,
)
from monopoly.rules import CLASSIC_RULES

SCHEMA_VERSION = 1
MAX_SAVE_BYTES = 5_000_000


def save_game(game: MonopolyGame, path: str | Path) -> Path:
    """Atomically save a complete game snapshot as human-readable JSON."""

    if game.rules != CLASSIC_RULES:
        raise InvalidSaveError("Saving custom rulesets is not supported by schema version 1")
    try:
        game.validate_invariants()
    except ValueError as error:
        raise InvalidSaveError(f"Cannot save an inconsistent game: {error}") from error

    temporary_name: str | None = None
    try:
        requested = Path(path).expanduser()
        if not requested.is_absolute():
            requested = Path.cwd() / requested
        if requested.is_symlink():
            raise InvalidSaveError("Refusing to overwrite a symbolic link")
        destination = requested.parent.resolve() / requested.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = _game_to_dict(game)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(
                payload,
                temporary,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            if os.fstat(temporary.fileno()).st_size > MAX_SAVE_BYTES:
                raise InvalidSaveError(
                    f"Save file exceeds the {MAX_SAVE_BYTES // 1_000_000} MB safety limit"
                )
        os.replace(temporary_name, destination)
        _sync_directory(destination.parent)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise InvalidSaveError(f"Cannot write save file: {error}") from error
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
    return destination


def load_game(path: str | Path) -> MonopolyGame:
    try:
        source = Path(path).expanduser().resolve()
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(source, flags)
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise InvalidSaveError("A save must be a regular file")
                encoded = handle.read(MAX_SAVE_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(encoded) > MAX_SAVE_BYTES:
            raise InvalidSaveError(
                f"Save file exceeds the {MAX_SAVE_BYTES // 1_000_000} MB safety limit"
            )
        raw = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except InvalidSaveError:
        raise
    except (
        OSError,
        UnicodeError,
        ValueError,
        RuntimeError,
        RecursionError,
    ) as error:
        raise InvalidSaveError(f"Cannot read save file: {error}") from error
    if not isinstance(raw, dict):
        raise InvalidSaveError("The save file root must be a JSON object")
    return _game_from_dict(raw)


def _game_to_dict(game: MonopolyGame) -> dict[str, Any]:
    state = game.state
    return {
        "schema_version": SCHEMA_VERSION,
        "ruleset": {"id": game.rules.name, "version": game.rules.version},
        "rng_state": _validated_rng_state(game.rng),
        "game": {
            "game_id": state.game_id,
            "players": [
                {
                    "id": player.id,
                    "name": player.name,
                    "cash": player.cash,
                    "position": player.position,
                    "in_jail": player.in_jail,
                    "jail_turns": player.jail_turns,
                    "jail_cards": {
                        deck.value: player.jail_cards.get(deck, 0) for deck in DeckKind
                    },
                    "bankrupt": player.bankrupt,
                }
                for player in state.players
            ],
            "properties": {
                str(position): {
                    "owner_id": asset.owner_id,
                    "houses": asset.houses,
                    "mortgaged": asset.mortgaged,
                }
                for position, asset in state.properties.items()
            },
            "decks": {
                deck_kind.value: list(deck.cards)
                for deck_kind, deck in state.decks.items()
            },
            "current_player_index": state.current_player_index,
            "phase": state.phase.value,
            "turn_number": state.turn_number,
            "doubles_in_turn": state.doubles_in_turn,
            "extra_turn": state.extra_turn,
            "last_roll": (
                [state.last_roll.first, state.last_roll.second] if state.last_roll else None
            ),
            "pending_property": state.pending_property,
            "bank_auction_queue": list(state.bank_auction_queue),
            "bank_houses": state.bank_houses,
            "bank_hotels": state.bank_hotels,
            "winner_id": state.winner_id,
            "history": list(state.history),
        },
    }


def _game_from_dict(raw: dict[str, Any]) -> MonopolyGame:
    try:
        schema_version = _integer(raw["schema_version"], "schema_version")
        if schema_version != SCHEMA_VERSION:
            raise InvalidSaveError(
                f"Unsupported save schema {schema_version}; expected {SCHEMA_VERSION}"
            )
        ruleset = _mapping(raw["ruleset"], "ruleset")
        ruleset_id = _string(ruleset.get("id"), "ruleset.id")
        ruleset_version = _integer(ruleset.get("version"), "ruleset.version")
        if ruleset_id != CLASSIC_RULES.name or ruleset_version != CLASSIC_RULES.version:
            raise InvalidSaveError("The save uses an unsupported ruleset")

        data = _mapping(raw["game"], "game")
        players_raw = _list(data["players"], "players")
        players: list[Player] = []
        for index, item in enumerate(players_raw):
            player_data = _mapping(item, f"players[{index}]")
            jail_cards_data = _mapping(
                player_data.get("jail_cards", {}), f"players[{index}].jail_cards"
            )
            players.append(
                Player(
                    id=_string(player_data["id"], f"players[{index}].id"),
                    name=_string(player_data["name"], f"players[{index}].name"),
                    cash=_integer(player_data["cash"], f"players[{index}].cash"),
                    position=_integer(player_data["position"], f"players[{index}].position"),
                    in_jail=_boolean(player_data["in_jail"], f"players[{index}].in_jail"),
                    jail_turns=_integer(
                        player_data["jail_turns"], f"players[{index}].jail_turns"
                    ),
                    jail_cards={
                        deck: _integer(
                            jail_cards_data.get(deck.value, 0),
                            f"players[{index}].jail_cards.{deck.value}",
                        )
                        for deck in DeckKind
                    },
                    bankrupt=_boolean(
                        player_data["bankrupt"], f"players[{index}].bankrupt"
                    ),
                )
            )

        properties_data = _mapping(data["properties"], "properties")
        properties: dict[int, PropertyState] = {}
        for raw_position, raw_asset in properties_data.items():
            if not raw_position.isascii() or not raw_position.isdigit():
                raise InvalidSaveError(f"Invalid property position key {raw_position!r}")
            position = int(raw_position)
            if str(position) != raw_position or position in properties:
                raise InvalidSaveError(f"Non-canonical property position key {raw_position!r}")
            asset_data = _mapping(raw_asset, f"properties.{raw_position}")
            owner_id = asset_data["owner_id"]
            if owner_id is not None:
                owner_id = _string(owner_id, f"properties.{raw_position}.owner_id")
            properties[position] = PropertyState(
                owner_id=owner_id,
                houses=_integer(asset_data["houses"], f"properties.{raw_position}.houses"),
                mortgaged=_boolean(
                    asset_data["mortgaged"], f"properties.{raw_position}.mortgaged"
                ),
            )

        decks_data = _mapping(data["decks"], "decks")
        decks = {
            deck: DeckState(
                [
                    _string(card, f"decks.{deck.value}")
                    for card in _list(decks_data[deck.value], f"decks.{deck.value}")
                ]
            )
            for deck in DeckKind
        }
        last_roll_data = data.get("last_roll")
        last_roll: DiceRoll | None = None
        if last_roll_data is not None:
            dice = _list(last_roll_data, "last_roll")
            if len(dice) != 2:
                raise InvalidSaveError("last_roll must contain exactly two dice")
            last_roll = DiceRoll(
                _integer(dice[0], "last_roll[0]"),
                _integer(dice[1], "last_roll[1]"),
            )

        pending_property = data.get("pending_property")
        if pending_property is not None:
            pending_property = _integer(pending_property, "pending_property")
        bank_auction_queue = [
            _integer(position, "bank_auction_queue item")
            for position in _list(data.get("bank_auction_queue", []), "bank_auction_queue")
        ]
        winner_id = data.get("winner_id")
        if winner_id is not None:
            winner_id = _string(winner_id, "winner_id")

        state = GameState(
            game_id=_string(data["game_id"], "game_id"),
            players=players,
            properties=properties,
            decks=decks,
            current_player_index=_integer(data["current_player_index"], "current_player_index"),
            phase=GamePhase(_string(data["phase"], "phase")),
            turn_number=_integer(data["turn_number"], "turn_number"),
            doubles_in_turn=_integer(data["doubles_in_turn"], "doubles_in_turn"),
            extra_turn=_boolean(data["extra_turn"], "extra_turn"),
            last_roll=last_roll,
            pending_property=pending_property,
            bank_auction_queue=bank_auction_queue,
            bank_houses=_integer(data["bank_houses"], "bank_houses"),
            bank_hotels=_integer(data["bank_hotels"], "bank_hotels"),
            winner_id=winner_id,
            history=[
                _string(item, "history item") for item in _list(data.get("history", []), "history")
            ],
        )
        rng = random.Random()
        rng.setstate(_tuplify(raw["rng_state"]))
        _validated_rng_state(rng)
        return MonopolyGame(state, rng=rng, rules=CLASSIC_RULES)
    except InvalidSaveError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise InvalidSaveError(f"Invalid save data: {error}") from error


def _tuplify(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuplify(item) for item in value)
    return value


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidSaveError(f"{path} must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidSaveError(f"{path} must be a list")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise InvalidSaveError(f"{path} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidSaveError(f"{path} contains invalid Unicode") from error
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise InvalidSaveError(f"{path} contains control characters")
    return value


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSaveError(f"{path} must be an integer")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidSaveError(f"{path} must be a boolean")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidSaveError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise InvalidSaveError(f"Non-standard JSON numeric constant: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InvalidSaveError("JSON floating-point values must be finite")
    return parsed


def _validated_rng_state(rng: random.Random) -> tuple[Any, ...]:
    state = rng.getstate()
    gaussian = state[2]
    if gaussian is not None and (
        isinstance(gaussian, bool)
        or not isinstance(gaussian, (int, float))
        or not math.isfinite(gaussian)
    ):
        raise InvalidSaveError("The random generator contains an invalid Gaussian cache")
    return state


def _sync_directory(directory: Path) -> None:
    """Best-effort durability for the rename on POSIX filesystems."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
