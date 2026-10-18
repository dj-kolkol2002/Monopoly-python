from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from monopoly.board import BOARD, SPACE_BY_POSITION, spaces_in_group
from monopoly.engine import MonopolyGame
from monopoly.models import ColorGroup, DeckKind, GamePhase, MonopolyError, SpaceKind, TradeOffer
from monopoly.persistence import load_game, save_game

GROUP_COLORS = {
    ColorGroup.BROWN: "#98633f",
    ColorGroup.LIGHT_BLUE: "#79c7d5",
    ColorGroup.PINK: "#d98fae",
    ColorGroup.ORANGE: "#e88d4a",
    ColorGroup.RED: "#d4544e",
    ColorGroup.YELLOW: "#e7c64d",
    ColorGroup.GREEN: "#4f9b75",
    ColorGroup.DARK_BLUE: "#436d9a",
}

TILE_NAMES = {
    "Mediterranean Avenue": "Med. Avenue",
    "Community Chest": "Community\nChest",
    "Reading Railroad": "Reading RR",
    "New York Avenue": "New York\nAvenue",
    "Pennsylvania Railroad": "Penn. RR",
    "Electric Company": "Electric\nCompany",
    "St. Charles Place": "St. Charles\nPlace",
    "Pennsylvania Avenue": "Penn.\nAvenue",
    "North Carolina Avenue": "N. Carolina\nAvenue",
    "Jail / Just Visiting": "Jail",
    "Free Parking": "Free\nParking",
    "Go To Jail": "Go To Jail",
    "B. & O. Railroad": "B. & O. RR",
}

APP_STYLE = """
QMainWindow, QDialog { background: #f2f0e8; color: #243832; }
QLabel { color: #243832; }
QPushButton {
    background: #176b58; color: #ffffff; border: 0; border-radius: 5px;
    padding: 9px 14px; font-weight: 600;
}
QPushButton:hover { background: #21846b; }
QPushButton:disabled { background: #bbc4bd; color: #eef0e9; }
QPushButton#secondary { background: #dce5dc; color: #244239; }
QPushButton#secondary:hover { background: #c9d9ce; }
QLineEdit, QSpinBox, QComboBox {
    background: #fffefa; color: #243832; border: 1px solid #b8c4ba;
    border-radius: 4px; padding: 6px;
}
QGroupBox {
    border: 1px solid #c8cec3; border-radius: 6px; margin-top: 12px;
    padding: 12px 8px 8px; font-weight: 700;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QTextEdit { background: #fffefa; border: 1px solid #c8cec3; border-radius: 5px; }
"""


class NewGameDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New game")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.count = QComboBox()
        self.count.addItems(["2", "3", "4"])
        form.addRow("Players", self.count)
        self.names = [QLineEdit() for _ in range(4)]
        self.name_labels: list[QLabel] = []
        for index, field in enumerate(self.names):
            label = QLabel(f"Player {index + 1}")
            self.name_labels.append(label)
            field.setPlaceholderText(f"Player {index + 1}")
            if index < 2:
                field.setText(["Player 1", "Player 2"][index])
            form.addRow(label, field)
        self.count.currentIndexChanged.connect(self._update_fields)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_fields()

    def _update_fields(self) -> None:
        count = int(self.count.currentText())
        for index, field in enumerate(self.names):
            field.setVisible(index < count)
            self.name_labels[index].setVisible(index < count)

    def player_names(self) -> list[str]:
        return [field.text().strip() for field in self.names[: int(self.count.currentText())]]


class TradeDialog(QDialog):
    def __init__(self, game: MonopolyGame, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.game = game
        self.setWindowTitle("Propose a trade")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.recipient = QComboBox()
        for player in game.state.players:
            if player.active and player.id != game.current_player.id:
                self.recipient.addItem(f"{player.name} (${player.cash})", player.id)
        form.addRow("Trade with", self.recipient)
        self.offer_cash = self._money_spin(game.current_player.cash)
        form.addRow("Cash you offer", self.offer_cash)
        self.offer_properties = QLineEdit()
        self.offer_properties.setPlaceholderText("For example: 1, 5, 12")
        form.addRow("Properties you offer", self.offer_properties)
        self.offer_chance = self._card_spin(game.current_player.jail_cards[DeckKind.CHANCE])
        form.addRow("Chance jail cards you offer", self.offer_chance)
        self.offer_chest = self._card_spin(game.current_player.jail_cards[DeckKind.COMMUNITY_CHEST])
        form.addRow("Community Chest jail cards you offer", self.offer_chest)

        recipient_id = self.recipient.currentData()
        recipient = game.player_by_id(recipient_id)
        self.request_cash = self._money_spin(recipient.cash)
        form.addRow("Cash you request", self.request_cash)
        self.request_properties = QLineEdit()
        self.request_properties.setPlaceholderText("For example: 3, 8, 15")
        form.addRow("Properties you request", self.request_properties)
        self.request_chance = self._card_spin(recipient.jail_cards[DeckKind.CHANCE])
        form.addRow("Chance jail cards requested", self.request_chance)
        self.request_chest = self._card_spin(recipient.jail_cards[DeckKind.COMMUNITY_CHEST])
        form.addRow("Community Chest jail cards requested", self.request_chest)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _money_spin(maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, maximum)
        spin.setSuffix(" $")
        return spin

    @staticmethod
    def _card_spin(maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, maximum)
        return spin

    @staticmethod
    def _positions(field: QLineEdit) -> tuple[int, ...]:
        value = field.text().strip()
        if not value:
            return ()
        return tuple(int(part.strip()) for part in value.split(","))

    def offer(self) -> TradeOffer:
        proposer = self.game.current_player
        return TradeOffer(
            proposer_id=proposer.id,
            recipient_id=self.recipient.currentData(),
            offered_cash=self.offer_cash.value(),
            requested_cash=self.request_cash.value(),
            offered_properties=self._positions(self.offer_properties),
            requested_properties=self._positions(self.request_properties),
            offered_jail_cards=(
                (DeckKind.CHANCE,) * self.offer_chance.value()
                + (DeckKind.COMMUNITY_CHEST,) * self.offer_chest.value()
            ),
            requested_jail_cards=(
                (DeckKind.CHANCE,) * self.request_chance.value()
                + (DeckKind.COMMUNITY_CHEST,) * self.request_chest.value()
            ),
        )


class MonopolyWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.game: MonopolyGame | None = None
        self.save_path: Path | None = None
        self.setWindowTitle("Monopoly | Classic rules")
        self.resize(1440, 920)
        self.setMinimumSize(1080, 720)
        self._build_menu()
        self._build_layout()
        self.setStyleSheet(APP_STYLE)
        self._show_landing()

    def _build_menu(self) -> None:
        game_menu = self.menuBar().addMenu("Game")
        new_action = QAction("New game", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.new_game)
        game_menu.addAction(new_action)
        open_action = QAction("Open save...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_game)
        game_menu.addAction(open_action)
        save_action = QAction("Save", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_game)
        game_menu.addAction(save_action)
        game_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        game_menu.addAction(quit_action)

    def _build_layout(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(20, 16, 20, 18)
        header = QHBoxLayout()
        title = QLabel("MONOPOLY")
        title.setFont(QFont("Georgia", 23, QFont.Weight.Bold))
        title.setStyleSheet("color: #176b58; letter-spacing: 2px")
        self.turn_label = QLabel("Classic rules  /  Local game")
        self.turn_label.setStyleSheet("color: #65776c; font-size: 14px")
        header.addWidget(title)
        header.addSpacing(18)
        header.addWidget(self.turn_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.new_button = QPushButton("New game")
        self.new_button.clicked.connect(self.new_game)
        self.open_button = QPushButton("Open save")
        self.open_button.setObjectName("secondary")
        self.open_button.clicked.connect(self.open_game)
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("secondary")
        self.save_button.clicked.connect(self.save_game)
        header.addWidget(self.new_button)
        header.addWidget(self.open_button)
        header.addWidget(self.save_button)
        shell.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(16)
        shell.addLayout(content, 1)

        self.board_frame = QFrame()
        self.board_frame.setStyleSheet("QFrame { background: #e4e8db; border-radius: 8px; }")
        board_layout = QGridLayout(self.board_frame)
        board_layout.setContentsMargins(8, 8, 8, 8)
        board_layout.setSpacing(4)
        self.tiles: dict[int, QPushButton] = {}
        for space in BOARD:
            row, column = self._board_coordinates(space.position)
            tile = QPushButton()
            tile.setMinimumSize(72, 64)
            tile.setMaximumSize(125, 110)
            tile.setSizePolicy(
                tile.sizePolicy().Policy.Expanding, tile.sizePolicy().Policy.Expanding
            )
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile.clicked.connect(
                lambda checked=False, position=space.position: self._inspect(position)
            )
            board_layout.addWidget(tile, row, column)
            self.tiles[space.position] = tile
        board_layout.addWidget(self._center_panel(), 1, 1, 9, 9)
        for index in range(11):
            board_layout.setRowStretch(index, 1 if index in {0, 10} else 2)
            board_layout.setColumnStretch(index, 1 if index in {0, 10} else 2)
        content.addWidget(self.board_frame, 7)

        side = QVBoxLayout()
        side.setSpacing(12)
        content.addLayout(side, 3)
        players_group = QGroupBox("Players")
        players_group.setMinimumHeight(170)
        players_layout = QVBoxLayout(players_group)
        self.players_list = QVBoxLayout()
        players_layout.addLayout(self.players_list)
        side.addWidget(players_group)

        actions_group = QGroupBox("Turn actions")
        actions_layout = QVBoxLayout(actions_group)
        self.action_hint = QLabel("Start a new game or open a saved game.")
        self.action_hint.setWordWrap(True)
        self.action_hint.setStyleSheet("color: #65776c")
        actions_layout.addWidget(self.action_hint)
        self.roll_button = self._action_button("Roll dice", self.roll)
        self.buy_button = self._action_button("Buy property", self.buy)
        self.auction_button = self._action_button("Start auction", self.auction)
        self.pay_button = self._action_button("Pay $50 to leave Jail", self.pay_jail)
        self.card_button = self._action_button(
            "Use a Get Out of Jail Free card", self.use_jail_card
        )
        self.manage_button = self._action_button("Manage properties", self.manage)
        self.trade_button = self._action_button("Propose a trade", self.trade)
        self.end_button = self._action_button("End turn", self.end_turn)
        for button in (
            self.roll_button,
            self.buy_button,
            self.auction_button,
            self.pay_button,
            self.card_button,
            self.manage_button,
            self.trade_button,
            self.end_button,
        ):
            actions_layout.addWidget(button)
        actions_layout.addStretch(1)
        side.addWidget(actions_group, 1)

        log_group = QGroupBox("Game log")
        log_layout = QVBoxLayout(log_group)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        log_layout.addWidget(self.log)
        side.addWidget(log_group, 1)

    def _center_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background: #f2f0e8; border-radius: 8px")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(12)
        kicker = QLabel("ATLANTIC CITY  /  CLASSIC EDITION")
        kicker.setStyleSheet("color: #718177; font-weight: 700; letter-spacing: 1px")
        kicker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center_title = QLabel("A city of your own")
        self.center_title.setFont(QFont("Georgia", 25, QFont.Weight.Bold))
        self.center_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center_title.setWordWrap(True)
        self.center_detail = QLabel("Buy streets. Build carefully. Stay solvent.")
        self.center_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.center_detail.setStyleSheet("color: #65776c; font-size: 14px")
        self.center_detail.setWordWrap(True)
        layout.addStretch(1)
        layout.addWidget(kicker)
        layout.addWidget(self.center_title)
        layout.addWidget(self.center_detail)
        layout.addStretch(1)
        return panel

    @staticmethod
    def _board_coordinates(position: int) -> tuple[int, int]:
        if position == 0:
            return 10, 10
        if position <= 10:
            return 10, 10 - position
        if position <= 20:
            return 20 - position, 0
        if position <= 30:
            return 0, position - 20
        return position - 30, 10

    @staticmethod
    def _action_button(text: str, callback: object) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    def _show_landing(self) -> None:
        for button in self._game_action_buttons():
            button.setEnabled(False)
        self.center_title.setText("A city of your own")
        self.center_detail.setText("Start a new game or open a saved game.")
        self._refresh_players()
        self._refresh_board()

    def _game_action_buttons(self) -> tuple[QPushButton, ...]:
        return (
            self.roll_button,
            self.buy_button,
            self.auction_button,
            self.pay_button,
            self.card_button,
            self.manage_button,
            self.trade_button,
            self.end_button,
            self.save_button,
        )

    def new_game(self) -> None:
        dialog = NewGameDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.game = MonopolyGame.new(dialog.player_names())
            self.save_path = None
            self.log.clear()
            self._refresh()
        except MonopolyError as error:
            self._show_error(error)

    def open_game(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open saved game", "", "Monopoly saves (*.json);;All files (*)"
        )
        if not filename:
            return
        try:
            self.game = load_game(filename)
            self.save_path = Path(filename)
            self.log.clear()
            self._refresh()
        except MonopolyError as error:
            self._show_error(error)

    def save_game(self) -> None:
        if self.game is None:
            return
        filename = str(self.save_path) if self.save_path else ""
        if not filename:
            filename, _ = QFileDialog.getSaveFileName(
                self, "Save game", "monopoly-save.json", "Monopoly saves (*.json)"
            )
        if not filename:
            return
        try:
            self.save_path = save_game(self.game, filename)
            self.statusBar().showMessage(f"Saved to {self.save_path}", 4000)
        except MonopolyError as error:
            self._show_error(error)

    def roll(self) -> None:
        if self.game is None:
            return
        try:
            result = self.game.roll_dice()
            self._refresh(f"Dice: {result.first} + {result.second} = {result.total}")
        except MonopolyError as error:
            self._show_error(error)

    def buy(self) -> None:
        if self.game is None:
            return
        try:
            self.game.buy_pending_property()
            self._refresh("Property purchased.")
        except MonopolyError as error:
            self._show_error(error)

    def auction(self) -> None:
        if self.game is None:
            return
        position = self.game.state.pending_property
        if position is None:
            return
        space = SPACE_BY_POSITION[position]
        bidders = [player for player in self.game.state.players if player.active]
        high_bid = 0
        leader_id: str | None = None
        passed: set[str] = set()
        while len(passed) < len(bidders):
            bidder = (
                next(
                    player
                    for player in bidders
                    if player.id not in passed and player.id != leader_id
                )
                if any(player.id not in passed and player.id != leader_id for player in bidders)
                else None
            )
            if bidder is None:
                break
            minimum = high_bid + 1
            bid, accepted = QInputDialog.getInt(
                self,
                f"Auction: {space.name}",
                f"{bidder.name}: bid at least ${minimum}, or enter 0 to pass.",
                minimum if bidder.cash >= minimum else 0,
                0,
                bidder.cash,
            )
            if not accepted or bid == 0:
                passed.add(bidder.id)
                continue
            if bid <= high_bid:
                continue
            high_bid = bid
            leader_id = bidder.id
            passed = {bidder.id}
        try:
            self.game.resolve_auction(leader_id, high_bid)
            self._refresh("Auction completed.")
        except MonopolyError as error:
            self._show_error(error)

    def pay_jail(self) -> None:
        if self.game is None:
            return
        try:
            self.game.pay_jail_fee()
            self._refresh("Jail fee paid.")
        except MonopolyError as error:
            self._show_error(error)

    def use_jail_card(self) -> None:
        if self.game is None:
            return
        try:
            self.game.use_jail_card()
            self._refresh("Get Out of Jail Free card used.")
        except MonopolyError as error:
            self._show_error(error)

    def manage(self) -> None:
        if self.game is None:
            return
        player = self.game.current_player
        positions = self.game.assets_of(player.id)
        if not positions:
            QMessageBox.information(self, "Manage properties", "You do not own any properties yet.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage properties")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        property_picker = QComboBox()
        for position in positions:
            space = SPACE_BY_POSITION[position]
            asset = self.game.state.properties[position]
            state = "mortgaged" if asset.mortgaged else f"buildings: {asset.houses}"
            property_picker.addItem(f"{space.name} ({state})", position)
        form.addRow("Property", property_picker)
        layout.addLayout(form)
        buttons_row = QHBoxLayout()
        actions = (
            ("Build", "build"),
            ("Sell building", "sell"),
            ("Mortgage", "mortgage"),
            ("Unmortgage", "unmortgage"),
        )
        for label, operation in actions:
            button = QPushButton(label)
            button.setObjectName("secondary")
            button.clicked.connect(
                lambda checked=False, action=operation: self._manage_property(
                    dialog, int(property_picker.currentData()), action
                )
            )
            buttons_row.addWidget(button)
        layout.addLayout(buttons_row)
        close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button.rejected.connect(dialog.reject)
        close_button.accepted.connect(dialog.accept)
        close_button.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _manage_property(self, dialog: QDialog, position: int, operation: str) -> None:
        if self.game is None:
            return
        player_id = self.game.current_player.id
        methods = {
            "build": self.game.build,
            "sell": self.game.sell_building,
            "mortgage": self.game.mortgage,
            "unmortgage": self.game.unmortgage,
        }
        try:
            methods[operation](player_id, position)
            dialog.accept()
            self._refresh(f"{operation.title()} completed.")
        except MonopolyError as error:
            self._show_error(error)

    def trade(self) -> None:
        if self.game is None:
            return
        dialog = TradeDialog(self.game, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            offer = dialog.offer()
            transferred = offer.offered_properties + offer.requested_properties
            mortgaged = tuple(
                position
                for position in transferred
                if self.game.state.properties[position].mortgaged
            )
            if mortgaged:
                payoff_dialog = QDialog(self)
                payoff_dialog.setWindowTitle("Mortgage options")
                payoff_layout = QVBoxLayout(payoff_dialog)
                payoff_layout.addWidget(
                    QLabel(
                        "The recipient pays 10% interest on mortgaged properties. "
                        "Select any properties to pay off immediately."
                    )
                )
                payoff_checks: dict[int, QCheckBox] = {}
                for position in mortgaged:
                    space = SPACE_BY_POSITION[position]
                    interest = (space.mortgage + 9) // 10
                    checkbox = QCheckBox(
                        f"{space.name}: pay ${space.mortgage + interest} (principal + 10% interest)"
                    )
                    payoff_checks[position] = checkbox
                    payoff_layout.addWidget(checkbox)
                payoff_buttons = QDialogButtonBox(
                    QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                )
                payoff_buttons.accepted.connect(payoff_dialog.accept)
                payoff_buttons.rejected.connect(payoff_dialog.reject)
                payoff_layout.addWidget(payoff_buttons)
                if payoff_dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                offer = replace(
                    offer,
                    unmortgage_on_transfer=tuple(
                        position
                        for position, checkbox in payoff_checks.items()
                        if checkbox.isChecked()
                    ),
                )
            recipient = self.game.player_by_id(offer.recipient_id)
            summary = (
                f"{recipient.name}, accept this trade?\n\n"
                f"Cash offered: ${offer.offered_cash}\n"
                f"Cash requested: ${offer.requested_cash}\n"
                f"Properties offered: {list(offer.offered_properties) or 'none'}\n"
                f"Properties requested: {list(offer.requested_properties) or 'none'}\n"
                f"Jail cards offered: {len(offer.offered_jail_cards)}\n"
                f"Jail cards requested: {len(offer.requested_jail_cards)}\n"
                f"Properties unmortgaged immediately: "
                f"{list(offer.unmortgage_on_transfer) or 'none'}"
            )
            if (
                QMessageBox.question(self, "Trade approval", summary)
                != QMessageBox.StandardButton.Yes
            ):
                return
            self.game.execute_trade(offer)
            self._refresh("Trade completed.")
        except (MonopolyError, ValueError) as error:
            self._show_error(error)

    def end_turn(self) -> None:
        if self.game is None:
            return
        try:
            self.game.end_turn()
            self._refresh()
        except MonopolyError as error:
            self._show_error(error)

    def _inspect(self, position: int) -> None:
        if self.game is None:
            return
        space = SPACE_BY_POSITION[position]
        description = [space.name]
        if space.is_ownable:
            asset = self.game.state.properties[position]
            owner = next(
                (player.name for player in self.game.state.players if player.id == asset.owner_id),
                "Bank",
            )
            description.extend((f"Price: ${space.price}", f"Owner: {owner}"))
            if space.kind == SpaceKind.STREET:
                description.append(f"Rent: ${space.rents[asset.houses]}")
                if (
                    asset.owner_id
                    and space.group
                    and self.game.owns_color_group(asset.owner_id, space.group)
                ):
                    description.append("Complete color group")
                if space.group:
                    members = ", ".join(item.name for item in spaces_in_group(space.group))
                    description.append(f"Color group: {members}")
            elif space.rents:
                description.append(
                    f"Rent schedule: {', '.join(f'${amount}' for amount in space.rents)}"
                )
            if asset.houses:
                description.append("Hotel" if asset.houses == 5 else f"Buildings: {asset.houses}")
            if asset.mortgaged:
                description.append("Mortgaged")
        elif space.tax:
            description.append(f"Tax: ${space.tax}")
        QMessageBox.information(self, f"Space {position}", "\n".join(description))

    def _refresh(self, message: str | None = None) -> None:
        if self.game is None:
            return
        state = self.game.state
        player = self.game.current_player
        self.turn_label.setText(f"Turn {state.turn_number}  /  {player.name} to play")
        if state.phase == GamePhase.GAME_OVER and state.winner_id:
            winner = self.game.player_by_id(state.winner_id)
            self.center_title.setText(f"{winner.name} wins")
            self.center_detail.setText("The game is over.")
        else:
            self.center_title.setText(player.name)
            self.center_detail.setText(
                f"${player.cash} cash  /  {SPACE_BY_POSITION[player.position].name}"
                + ("  /  In Jail" if player.in_jail else "")
            )
        phase_hints = {
            GamePhase.AWAITING_ROLL: "Roll the dice to begin your turn.",
            GamePhase.AWAITING_PURCHASE: "Buy the unowned property or send it to auction.",
            GamePhase.AWAITING_AUCTION: "The Bank is auctioning a property from a bankrupt estate.",
            GamePhase.ACTIONS: "Manage assets, trade, or end your turn.",
            GamePhase.GAME_OVER: "The game is complete.",
        }
        self.action_hint.setText(phase_hints[state.phase])
        self.roll_button.setEnabled(state.phase == GamePhase.AWAITING_ROLL)
        self.buy_button.setEnabled(state.phase == GamePhase.AWAITING_PURCHASE)
        self.auction_button.setEnabled(
            state.phase in {GamePhase.AWAITING_PURCHASE, GamePhase.AWAITING_AUCTION}
        )
        self.pay_button.setEnabled(state.phase == GamePhase.AWAITING_ROLL and player.in_jail)
        self.card_button.setEnabled(
            state.phase == GamePhase.AWAITING_ROLL
            and player.in_jail
            and any(player.jail_cards.values())
        )
        management_phase = state.phase in {
            GamePhase.AWAITING_ROLL,
            GamePhase.AWAITING_PURCHASE,
            GamePhase.ACTIONS,
        }
        self.manage_button.setEnabled(management_phase)
        self.trade_button.setEnabled(
            management_phase and sum(other.active for other in state.players) > 1
        )
        self.end_button.setEnabled(state.phase == GamePhase.ACTIONS)
        self.save_button.setEnabled(True)
        self._refresh_players()
        self._refresh_board()
        if message:
            self.statusBar().showMessage(message, 5000)
            if self.save_path is not None:
                try:
                    self.save_path = save_game(self.game, self.save_path)
                except MonopolyError as error:
                    self.statusBar().showMessage(f"Autosave failed: {error}", 8000)
        history = "\n".join(state.history[-100:])
        self.log.setPlainText(history)
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _refresh_players(self) -> None:
        while self.players_list.count():
            item = self.players_list.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        if self.game is None:
            label = QLabel("Start a game to see player standings.")
            label.setWordWrap(True)
            self.players_list.addWidget(label)
            return
        for player in self.game.state.players:
            row = QLabel(
                f"<b>{player.name}</b>  ${player.cash}<br>"
                f"Space {player.position}: {SPACE_BY_POSITION[player.position].name}"
                + (" &nbsp; <span style='color:#a34b3d'>IN JAIL</span>" if player.in_jail else "")
                + (" &nbsp; <span style='color:#a34b3d'>BANKRUPT</span>" if player.bankrupt else "")
            )
            row.setWordWrap(True)
            row.setStyleSheet(
                "padding: 8px; background: #fffefa; border-left: 4px solid #176b58; "
                "border-radius: 4px;"
                if player.id == self.game.current_player.id and player.active
                else (
                    "padding: 8px; background: #fffefa; border-left: 4px solid #c8cec3; "
                    "border-radius: 4px;"
                )
            )
            self.players_list.addWidget(row)
        self.players_list.addStretch(1)

    def _refresh_board(self) -> None:
        for space in BOARD:
            tile = self.tiles[space.position]
            owner_line = ""
            player_markers = ""
            color = GROUP_COLORS.get(space.group, "#d7e2d8") if space.group else "#d7e2d8"
            text_color = "#203a31"
            if self.game:
                asset = self.game.state.properties.get(space.position)
                if asset and asset.owner_id:
                    owner = next(
                        (
                            candidate.name
                            for candidate in self.game.state.players
                            if candidate.id == asset.owner_id
                        ),
                        "",
                    )
                    owner_line = f"\n{owner[:7]}"
                    if asset.mortgaged:
                        owner_line += "\nM"
                    elif asset.houses == 5:
                        owner_line += "\nH"
                    elif asset.houses:
                        owner_line += f"\nH{asset.houses}"
                markers = [
                    candidate.name[:1].upper()
                    for candidate in self.game.state.players
                    if candidate.active and candidate.position == space.position
                ]
                if markers:
                    player_markers = "\n● " + "  ".join(markers)
                if self.game.state.pending_property == space.position:
                    tile.setStyleSheet(
                        "background: #f5d887; color: #253a32; "
                        "border: 2px solid #b87b26; border-radius: 4px; font-size: 10px;"
                    )
                else:
                    tile.setStyleSheet(
                        f"background: {color}; color: {text_color}; "
                        "border: 1px solid #ffffff; border-radius: 4px; font-size: 10px; "
                        "font-weight: 600; padding: 3px;"
                    )
            else:
                tile.setStyleSheet(
                    f"background: {color}; color: {text_color}; "
                    "border: 1px solid #ffffff; border-radius: 4px; font-size: 10px; "
                    "font-weight: 600; padding: 3px;"
                )
            label = TILE_NAMES.get(space.name, space.name.replace(" ", "\n"))
            tile.setText(f"{space.position:02d}\n{label}{owner_line}{player_markers}")
            tile.setToolTip(f"{space.position}. {space.name}")

    def _show_error(self, error: Exception) -> None:
        QMessageBox.warning(self, "Action could not be completed", str(error))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Monopoly")
    app.setStyle("Fusion")
    window = MonopolyWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
