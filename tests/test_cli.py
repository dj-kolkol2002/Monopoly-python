from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monopoly.cli import ConsoleGame, _interactive_setup
from monopoly.engine import MonopolyGame
from monopoly.persistence import load_game


class ConsoleTests(unittest.TestCase):
    def test_setup_reprompts_invalid_count_and_names(self) -> None:
        answers = iter(["abc", "5", "2", "", "Ala", "ala", "Bartek", ""])
        with patch("builtins.input", side_effect=answers), patch("sys.stdout", new=io.StringIO()):
            game, autosave = _interactive_setup()

        self.assertEqual([player.name for player in game.state.players], ["Ala", "Bartek"])
        self.assertIsNone(autosave)

    def test_manual_save_becomes_autosave_and_can_be_loaded(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek"], seed=17)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game.json"
            answers = iter([f'save "{path}"', "exit"])
            output = io.StringIO()
            with patch("builtins.input", side_effect=answers), patch("sys.stdout", new=output):
                ConsoleGame(game).run()

            loaded = load_game(path)

        self.assertEqual(loaded.state.game_id, game.state.game_id)
        self.assertIn("Game saved", output.getvalue())

    def test_save_preserves_windows_path_backslashes_and_spaces(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek"])
        console = ConsoleGame(game)
        path = Path(r"C:\Users\Alice\My Game.json")

        with patch("monopoly.cli.save_game", return_value=path) as save_mock:
            console._dispatch(r'save "C:\Users\Alice\My Game.json"')

        save_mock.assert_called_once_with(game, path)

    def test_failed_autosave_allows_explicit_exit_without_saving(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek"])
        with tempfile.TemporaryDirectory() as directory:
            # A directory cannot be replaced by the JSON snapshot file.
            answers = iter(["exit", "yes"])
            output = io.StringIO()
            with patch("builtins.input", side_effect=answers), patch("sys.stdout", new=output):
                ConsoleGame(game, Path(directory)).run()

        self.assertIn("Could not save the game", output.getvalue())

    def test_trade_wizard_can_be_cancelled(self) -> None:
        game = MonopolyGame.new(["Ala", "Bartek"])
        console = ConsoleGame(game)
        output = io.StringIO()
        with patch("builtins.input", return_value="cancel"), patch("sys.stdout", new=output):
            console._trade()

        self.assertIn("Trade cancelled", output.getvalue())
        self.assertEqual([player.cash for player in game.state.players], [1_500, 1_500])


if __name__ == "__main__":
    unittest.main()
