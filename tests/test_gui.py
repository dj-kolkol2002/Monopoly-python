from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from monopoly.engine import MonopolyGame
from monopoly.gui import MonopolyWindow, NewGameDialog
from monopoly.models import GamePhase


class QtGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_new_game_dialog_shows_player_fields_for_selected_count(self) -> None:
        dialog = NewGameDialog()

        self.assertEqual(dialog.player_names(), ["Player 1", "Player 2"])
        dialog.count.setCurrentText("4")
        dialog.names[2].setText("Player 3")
        dialog.names[3].setText("Player 4")

        self.assertEqual(len(dialog.player_names()), 4)
        self.assertTrue(dialog.names[3].isVisibleTo(dialog))
        dialog.count.setCurrentText("2")
        self.assertFalse(dialog.names[3].isVisibleTo(dialog))
        dialog.close()

    def test_window_renders_board_and_tracks_game_phase(self) -> None:
        window = MonopolyWindow()
        window.game = MonopolyGame.new(["Alice", "Bob"], seed=2)
        window._refresh()

        self.assertEqual(len(window.tiles), 40)
        self.assertTrue(window.roll_button.isEnabled())
        window.game.roll_dice((1, 2))
        window._refresh()

        self.assertEqual(window.game.state.phase, GamePhase.AWAITING_PURCHASE)
        self.assertTrue(window.buy_button.isEnabled())
        self.assertEqual(window.game.state.pending_property, 3)
        window.buy()
        self.assertEqual(window.game.state.properties[3].owner_id, window.game.current_player.id)
        self.assertEqual(window.game.state.phase, GamePhase.ACTIONS)
        self.assertTrue(window.end_button.isEnabled())
        window.close()

    def test_opened_save_autosaves_after_a_game_action(self) -> None:
        window = MonopolyWindow()
        window.game = MonopolyGame.new(["Alice", "Bob"], seed=2)
        path = Path("existing-save.json")

        with patch("monopoly.gui.save_game") as save_mock:
            window.save_path = path
            window.game.roll_dice((1, 2))
            window._refresh("Dice rolled")

        save_mock.assert_called_once_with(window.game, path)
        window.close()


if __name__ == "__main__":
    unittest.main()
