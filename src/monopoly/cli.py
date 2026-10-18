from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from monopoly.board import BOARD, SPACE_BY_POSITION
from monopoly.engine import MonopolyGame
from monopoly.models import (
    DeckKind,
    GamePhase,
    InvalidActionError,
    MonopolyError,
    Player,
    TradeOffer,
)
from monopoly.persistence import load_game, save_game


class ConsoleGame:
    def __init__(self, game: MonopolyGame, autosave: Path | None = None) -> None:
        self.game = game
        self.autosave = autosave
        self._shown_history = max(0, len(game.state.history) - 5)

    def run(self) -> None:
        print("\nMONOPOLY - local game for 2-4 players")
        print("Type 'help' to see available commands.\n")
        self._show_events()
        try:
            while self.game.state.phase != GamePhase.GAME_OVER:
                self._show_turn_header()
                raw = input(self._prompt()).strip()
                if not raw:
                    continue
                try:
                    revision_before = self.game.revision
                    should_quit = self._dispatch(raw)
                    if should_quit:
                        return
                    if self.game.revision != revision_before:
                        self._autosave()
                    self._show_events()
                except MonopolyError as error:
                    print(f"Error: {error}")
                except ValueError:
                    print("Error: enter a valid amount or property number.")
        except (EOFError, KeyboardInterrupt):
            print("\nGame interrupted.")
            try:
                self._autosave()
            except MonopolyError as error:
                print(f"Autosave failed: {error}")

        if self.game.state.winner_id is not None:
            winner = self.game.player_by_id(self.game.state.winner_id)
            self._show_events()
            print(f"\n🏆 {winner.name} wins!")

    def _dispatch(self, raw: str) -> bool:
        parts = raw.split(maxsplit=1)
        command = parts[0].casefold()
        arguments = parts[1] if len(parts) > 1 else ""

        if command in {"help", "?"}:
            self._show_help()
        elif command in {"status", "s"}:
            self._show_status()
        elif command in {"board", "b"}:
            self._show_board()
        elif command in {"assets", "a"}:
            self._show_assets(self.game.current_player.id)
        elif command == "save":
            path = arguments.strip()
            if len(path) >= 2 and path[0] == path[-1] and path[0] in {'"', "'"}:
                path = path[1:-1]
            destination = Path(path) if path else self._default_save_path()
            saved = save_game(self.game, destination)
            self.autosave = saved
            print(f"Game saved: {saved}")
        elif command in {"quit", "exit", "q"}:
            if self.autosave is not None:
                try:
                    saved = save_game(self.game, self.autosave)
                    print(f"Game saved: {saved}")
                except MonopolyError as error:
                    print(f"Could not save the game: {error}")
                    answer = input("Quit without saving anyway? [yes/no] ").strip().casefold()
                    return answer in {"yes", "y"}
            return True
        elif self.game.state.phase == GamePhase.AWAITING_ROLL:
            self._dispatch_before_roll(command)
        elif self.game.state.phase == GamePhase.AWAITING_PURCHASE:
            self._dispatch_purchase(command)
        elif self.game.state.phase == GamePhase.AWAITING_AUCTION:
            self._dispatch_bank_auction(command)
        elif self.game.state.phase == GamePhase.ACTIONS:
            self._dispatch_actions(command)
        else:
            raise InvalidActionError("Unknown command for the current game phase")
        return False

    def _dispatch_before_roll(self, command: str) -> None:
        player = self.game.current_player
        if command in {"roll", "r"}:
            self.game.roll_dice()
        elif command == "pay":
            self.game.pay_jail_fee()
        elif command == "card":
            self.game.use_jail_card()
        elif command in {"manage", "m"}:
            self._manage_assets()
        elif command in {"trade", "t"}:
            self._trade()
        elif player.in_jail:
            raise InvalidActionError("Use: roll, pay, card, manage, or trade")
        else:
            raise InvalidActionError("Use: roll, manage, trade, status, or help")

    def _dispatch_purchase(self, command: str) -> None:
        if command in {"buy", "b"}:
            self.game.buy_pending_property()
        elif command in {"auction", "a"}:
            self._run_auction()
        elif command in {"manage", "m"}:
            self._manage_assets()
        elif command in {"trade", "t"}:
            self._trade()
        else:
            raise InvalidActionError("Choose: buy, auction, manage, or trade")

    def _dispatch_actions(self, command: str) -> None:
        if command in {"end", "e"}:
            self.game.end_turn()
        elif command in {"manage", "m"}:
            self._manage_assets()
        elif command in {"trade", "t"}:
            self._trade()
        else:
            raise InvalidActionError("Use: end, manage, trade, status, or help")

    def _dispatch_bank_auction(self, command: str) -> None:
        if command in {"auction", "a"}:
            self._run_auction()
        else:
            raise InvalidActionError("The Bank is waiting for the auction to finish")

    def _run_auction(self) -> None:
        position = self.game.state.pending_property
        if position is None:
            raise InvalidActionError("There is no property to auction")
        space = SPACE_BY_POSITION[position]
        bidders = [player for player in self.game.state.players if player.active]
        active_ids = {player.id for player in bidders}
        current_bid = 0
        leader_id: str | None = None
        index = 0
        print(f"\nAuction: {space.name}. Minimum opening bid: $1.")
        print("Enter an amount higher than the current bid, or 'pass'.")

        while active_ids:
            if leader_id is not None and active_ids == {leader_id}:
                break
            bidder = bidders[index % len(bidders)]
            index += 1
            if bidder.id not in active_ids or bidder.id == leader_id:
                continue
            maximum = bidder.cash
            answer = (
                input(f"{bidder.name} (bid ${current_bid}, max ${maximum}): ").strip().casefold()
            )
            if answer in {"pass", "p"}:
                active_ids.remove(bidder.id)
                continue
            try:
                bid = int(answer)
            except ValueError:
                print("Enter a whole-dollar amount or 'pass'.")
                continue
            if bid <= current_bid:
                print("The new bid must be higher than the current bid.")
                continue
            if bid > maximum:
                print("You do not have enough cash for that bid.")
                continue
            current_bid = bid
            leader_id = bidder.id

        self.game.resolve_auction(leader_id, current_bid if leader_id else 0)

    def _manage_assets(self) -> None:
        player = self.game.current_player
        while True:
            print()
            self._show_assets(player.id)
            print("Commands: build N | sell N | mortgage N | unmortgage N | back")
            raw = input("assets> ").strip()
            if not raw:
                continue
            parts = raw.casefold().split()
            if parts[0] in {"back", "b"}:
                return
            if parts[0] in {"status"}:
                self._show_status()
                continue
            if parts[0] == "board":
                self._show_board()
                continue
            if parts[0] == "save":
                destination = Path(parts[1]) if len(parts) > 1 else self._default_save_path()
                saved = save_game(self.game, destination)
                self.autosave = saved
                print(f"Game saved: {saved}")
                continue
            if len(parts) != 2:
                print("Enter a command and property number, e.g. 'mortgage 5'.")
                continue
            try:
                position = int(parts[1])
                if parts[0] in {"build", "bld"}:
                    self.game.build(player.id, position)
                elif parts[0] in {"sell", "s"}:
                    self.game.sell_building(player.id, position)
                elif parts[0] in {"mortgage", "m"}:
                    self.game.mortgage(player.id, position)
                elif parts[0] in {"unmortgage", "u"}:
                    self.game.unmortgage(player.id, position)
                else:
                    print("Unknown asset-management command.")
                    continue
                self._autosave()
                self._show_events()
            except MonopolyError as error:
                print(f"Error: {error}")
            except ValueError:
                print("The property number must be an integer.")

    def _trade(self) -> None:
        try:
            self._prepare_trade()
        except _UserCancelled:
            print("Trade cancelled.")

    def _prepare_trade(self) -> None:
        proposer = self.game.current_player
        recipients = [
            player
            for player in self.game.state.players
            if player.active and player.id != proposer.id
        ]
        if not recipients:
            raise InvalidActionError("There are no players available to trade with")
        print("\nPartnerzy handlowi:")
        for number, player in enumerate(recipients, start=1):
            print(f"  {number}. {player.name} (${player.cash})")
        choice_raw = input("Choose a player number (or 'cancel'): ").strip()
        self._cancel_if_requested(choice_raw)
        choice = int(choice_raw)
        if not 1 <= choice <= len(recipients):
            raise InvalidActionError("Invalid player number")
        recipient = recipients[choice - 1]

        self._show_assets(proposer.id)
        offered_cash = self._read_nonnegative("How much cash are you offering? ")
        offered_properties = self._read_positions(
            "Property numbers you are offering (comma-separated): "
        )
        offered_cards = self._read_cards(proposer, "offering")

        self._show_assets(recipient.id)
        requested_cash = self._read_nonnegative("How much cash are you requesting? ")
        requested_properties = self._read_positions(
            "Property numbers you are requesting (comma-separated): "
        )
        requested_cards = self._read_cards(recipient, "requesting")

        transferred = offered_properties + requested_properties
        mortgaged = [
            position for position in transferred if self.game.state.properties[position].mortgaged
        ]
        immediate_payoffs: tuple[int, ...] = ()
        if mortgaged:
            immediate_payoffs = self._read_positions(
                "Mortgaged properties: "
                f"{mortgaged}. Which should the recipient unmortgage immediately? (Enter = none): "
            )
            if not set(immediate_payoffs) <= set(mortgaged):
                raise InvalidActionError(
                    "Only transferred mortgaged properties can be unmortgaged immediately"
                )

        offer = TradeOffer(
            proposer_id=proposer.id,
            recipient_id=recipient.id,
            offered_cash=offered_cash,
            requested_cash=requested_cash,
            offered_properties=offered_properties,
            requested_properties=requested_properties,
            offered_jail_cards=offered_cards,
            requested_jail_cards=requested_cards,
            unmortgage_on_transfer=immediate_payoffs,
        )
        print("\nTrade summary:")
        print(
            f"  {proposer.name} offers: ${offered_cash}, "
            f"properties {list(offered_properties) or 'none'}, {len(offered_cards)} cards"
        )
        print(
            f"  {recipient.name} offers: ${requested_cash}, "
            f"properties {list(requested_properties) or 'none'}, {len(requested_cards)} cards"
        )
        if mortgaged:
            print(
                "  Note: the recipient pays 10% interest immediately on each mortgaged property. "
                f"Properties: {mortgaged}."
            )
            if immediate_payoffs:
                print(
                    "  Immediate payoff (principal + initial 10% interest): "
                    f"{list(immediate_payoffs)}."
                )
        print(f"{recipient.name}: accept this trade? [yes/no]")
        if input("> ").strip().casefold() not in {"yes", "y"}:
            print("Trade declined.")
            return
        self.game.execute_trade(offer)

    def _show_turn_header(self) -> None:
        player = self.game.current_player
        jail = " | IN JAIL" if player.in_jail else ""
        print(
            f"\nTura {self.game.state.turn_number} | {player.name} | "
            f"${player.cash} | pole {player.position}: "
            f"{SPACE_BY_POSITION[player.position].name}{jail}"
        )

    def _prompt(self) -> str:
        phase = self.game.state.phase
        if phase == GamePhase.AWAITING_ROLL:
            if self.game.current_player.in_jail:
                return "[roll/pay/card/manage/trade]> "
            return "[roll/manage/trade]> "
        if phase == GamePhase.AWAITING_PURCHASE:
            position = self.game.state.pending_property
            space = SPACE_BY_POSITION[position] if position is not None else None
            label = f"{space.name} ${space.price}" if space else "property"
            return f"[{label}: buy/auction/manage]> "
        if phase == GamePhase.AWAITING_AUCTION:
            position = self.game.state.pending_property
            space = SPACE_BY_POSITION[position] if position is not None else None
            label = space.name if space else "Bank property"
            return f"[Bank auction: {label} - type 'auction']> "
        return "[end/manage/trade]> "

    def _show_status(self) -> None:
        print(
            f"\nGame {self.game.state.game_id} | turn {self.game.state.turn_number} | "
            f"phase: {self.game.state.phase.value}"
        )
        for player in self.game.state.players:
            flags = []
            if player.in_jail:
                flags.append("in jail")
            if player.bankrupt:
                flags.append("bankrupt")
            suffix = f" ({', '.join(flags)})" if flags else ""
            print(
                f"  {player.name}: ${player.cash}, space {player.position} "
                f"{SPACE_BY_POSITION[player.position].name}, "
                f"assets ${self.game.liquidation_value(player.id)}{suffix}"
            )
        print(f"  Bank: {self.game.state.bank_houses} houses, {self.game.state.bank_hotels} hotels")

    def _show_assets(self, player_id: str) -> None:
        player = self.game.player_by_id(player_id)
        print(f"Assets for {player.name}: cash ${player.cash}")
        positions = self.game.assets_of(player_id)
        if not positions:
            print("  (no properties)")
        for position in positions:
            space = SPACE_BY_POSITION[position]
            asset = self.game.state.properties[position]
            flags = []
            if asset.houses:
                flags.append("hotel" if asset.houses == 5 else f"houses: {asset.houses}")
            if asset.mortgaged:
                flags.append("MORTGAGED")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            deed = f"mortgage ${space.mortgage}"
            if space.house_cost:
                deed += f", building ${space.house_cost}, rents {'/'.join(map(str, space.rents))}"
            elif space.rents:
                deed += f", rents {'/'.join(map(str, space.rents))}"
            else:
                deed += ", rent 4x/10x dice roll"
            print(f"  {position:>2}: {space.name}{suffix} ({deed})")
        chance = player.jail_cards[DeckKind.CHANCE]
        chest = player.jail_cards[DeckKind.COMMUNITY_CHEST]
        if chance or chest:
            print(f"  Get Out of Jail Free cards: Chance {chance}, Community Chest {chest}")

    def _show_board(self) -> None:
        owners = {player.id: player.name for player in self.game.state.players}
        print()
        for space in BOARD:
            details = ""
            if space.is_ownable:
                asset = self.game.state.properties[space.position]
                owner = owners.get(asset.owner_id or "", "Bank")
                details = f" | ${space.price} | owner: {owner}"
                if asset.houses:
                    details += f" | buildings: {asset.houses}"
                if asset.mortgaged:
                    details += " | MORTGAGED"
            print(f"{space.position:>2}. {space.name}{details}")

    def _show_help(self) -> None:
        print(
            """
General commands:
    status, board, assets          show game information
    save [file.json]               save the game
    quit                           exit (autosaves when configured)

Turn:
    roll                           roll both dice
    buy / auction                  decide after landing on an unowned property
    manage                         build, sell, or mortgage property
    trade                          trade with another player
    end                            end your turn

In Jail: enter 'pay' or 'card' before rolling.
See README.md for the rules and startup instructions.
""".strip()
        )

    def _show_events(self) -> None:
        events = self.game.state.history[self._shown_history :]
        for event in events:
            print(f"• {event}")
        self._shown_history = len(self.game.state.history)

    def _read_positions(self, prompt: str) -> tuple[int, ...]:
        raw = input(prompt).strip()
        self._cancel_if_requested(raw)
        if not raw:
            return ()
        positions = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
        if any(position not in self.game.state.properties for position in positions):
            raise InvalidActionError("A trade can only include properties on the board")
        return positions

    def _read_nonnegative(self, prompt: str) -> int:
        raw = input(prompt).strip()
        self._cancel_if_requested(raw)
        value = int(raw or "0")
        if value < 0:
            raise InvalidActionError("An amount cannot be negative")
        return value

    def _read_cards(self, player: Player, verb: str) -> tuple[DeckKind, ...]:
        jail_cards = player.jail_cards
        result: list[DeckKind] = []
        for deck, label in (
            (DeckKind.CHANCE, "Chance"),
            (DeckKind.COMMUNITY_CHEST, "Community Chest"),
        ):
            available = jail_cards[deck]
            if available:
                count = self._read_nonnegative(
                    f"How many '{label}: Get Out of Jail Free' cards are you {verb}? "
                    f"(you have {available}) "
                )
                if count > available:
                    raise InvalidActionError("You cannot trade more cards than you have")
                result.extend([deck] * count)
        return tuple(result)

    @staticmethod
    def _cancel_if_requested(raw: str) -> None:
        if raw.casefold() in {"cancel", "back"}:
            raise _UserCancelled

    def _default_save_path(self) -> Path:
        return self.autosave or Path("saves") / f"{self.game.state.game_id}.json"

    def _autosave(self) -> None:
        if self.autosave is not None:
            save_game(self.game, self.autosave)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classic Monopoly for 2-4 local players")
    subparsers = parser.add_subparsers(dest="command")

    new_parser = subparsers.add_parser("new", help="start a new game")
    new_parser.add_argument("players", nargs="+", help="player names (2-4)")
    new_parser.add_argument("--seed", type=int, help="random seed for tests or repeatable games")
    new_parser.add_argument("--autosave", type=Path, help="autosave file")

    load_parser = subparsers.add_parser("load", help="load a saved game")
    load_parser.add_argument("save_file", type=Path)
    load_parser.add_argument(
        "--autosave",
        type=Path,
        help="autosave destination (defaults to overwriting the loaded save)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "new":
            game = MonopolyGame.new(arguments.players, seed=arguments.seed)
            autosave = arguments.autosave
        elif arguments.command == "load":
            game = load_game(arguments.save_file)
            autosave = arguments.autosave or arguments.save_file
        else:
            game, autosave = _interactive_setup()
        ConsoleGame(game, autosave).run()
        return 0
    except MonopolyError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except (EOFError, ValueError) as error:
        print(f"Invalid input: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nSetup interrupted.", file=sys.stderr)
        return 130


def _interactive_setup() -> tuple[MonopolyGame, Path | None]:
    print("New Monopoly game")
    while True:
        try:
            count = int(input("Number of players [2-4]: "))
        except ValueError:
            print("Enter a whole number from 2 to 4.")
            continue
        if 2 <= count <= 4:
            break
        print("A game requires 2 to 4 players.")
    names: list[str] = []
    for number in range(1, count + 1):
        while True:
            name = input(f"Player {number}'s name: ").strip()
            if not name:
                print("A name cannot be empty.")
            elif name.casefold() in {item.casefold() for item in names}:
                print("Player names must be unique.")
            else:
                names.append(name)
                break
    autosave_raw = input("Autosave file (press Enter to disable autosaving): ").strip()
    autosave = Path(autosave_raw) if autosave_raw else None
    return MonopolyGame.new(names), autosave


class _UserCancelled(Exception):
    pass


if __name__ == "__main__":
    raise SystemExit(main())
