from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from monopoly.engine import MonopolyGame
from monopoly.models import GamePhase, InvalidSaveError
from monopoly.persistence import load_game, save_game


class PersistenceTests(unittest.TestCase):
    def test_round_trip_preserves_pending_decision_and_rng(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek", "Celina"], seed=123)
        game.roll_dice((1, 2))
        self.assertEqual(game.state.phase, GamePhase.AWAITING_PURCHASE)

        with tempfile.TemporaryDirectory() as directory:
            path = save_game(game, Path(directory) / "nested" / "game.json")
            loaded = load_game(path)

        self.assertEqual(loaded.state.game_id, game.state.game_id)
        self.assertEqual(loaded.state.pending_property, 3)
        self.assertEqual(loaded.state.phase, GamePhase.AWAITING_PURCHASE)
        self.assertEqual(loaded.state.decks, game.state.decks)
        self.assertEqual(loaded.rng.getstate(), game.rng.getstate())
        loaded.validate_invariants()

    def test_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(InvalidSaveError):
                load_game(path)

    def test_rejects_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = save_game(
                MonopolyGame.new(["A", "B"], seed=1), Path(directory) / "game.json"
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            data["schema_version"] = 999
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(InvalidSaveError):
                load_game(path)

    def test_rejects_invalid_cross_field_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = save_game(
                MonopolyGame.new(["A", "B"], seed=1), Path(directory) / "game.json"
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            data["game"]["phase"] = "awaiting_purchase"
            data["game"]["pending_property"] = 999
            path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(InvalidSaveError):
                load_game(path)

    def test_rejects_noncanonical_property_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = save_game(
                MonopolyGame.new(["A", "B"], seed=1), Path(directory) / "game.json"
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            data["game"]["properties"]["01"] = data["game"]["properties"].pop("1")
            path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(InvalidSaveError):
                load_game(path)

    def test_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")

            with self.assertRaises(InvalidSaveError):
                load_game(path)

    def test_write_errors_are_reported_as_domain_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(InvalidSaveError):
                save_game(MonopolyGame.new(["A", "B"]), Path(directory))

    def test_refuses_state_that_cannot_round_trip(self) -> None:
        game = MonopolyGame.new(["A", "B"])
        game.current_player.cash = True  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as directory, self.assertRaises(InvalidSaveError):
            save_game(game, Path(directory) / "game.json")


if __name__ == "__main__":
    unittest.main()
