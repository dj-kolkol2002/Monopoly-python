"""Modern, rules-focused Monopoly engine."""

from monopoly.engine import MonopolyGame
from monopoly.models import GamePhase, Player, Space, SpaceKind
from monopoly.persistence import load_game, save_game

__all__ = [
    "GamePhase",
    "MonopolyGame",
    "Player",
    "Space",
    "SpaceKind",
    "load_game",
    "save_game",
]

__version__ = "0.1.0"

