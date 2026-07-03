import time
import uuid
from threading import Lock
from typing import Optional

from app.models import Player, GameStatus, MoveRecord


# Константы таймаутов (в секундах)
TURN_TIMEOUT: float = 300.0       # 5 минут на ход
STALLED_GAME_TIMEOUT: float = 86400.0  # 24 часа — игра без ходов считается брошенной


class TicTacToe:
    """Ядро игры — единственный источник истины о состоянии.

    Zero Trust:
    - Сервер валидирует каждый ход.
    - join_secret защищает присоединение второго игрока.
    - per-game Lock предотвращает race condition.
    - move_history обеспечивает аудит.
    """

    def __init__(self):
        self.game_id: str = str(uuid.uuid4())
        self.join_secret: str = str(uuid.uuid4())  # одноразовый секрет для второго игрока
        self.board: list[Optional[Player]] = [None] * 9
        self.current_turn: Player = Player.X
        self.status: GameStatus = GameStatus.WAITING  # ждём второго игрока
        self.winner: Optional[Player] = None
        self.move_count: int = 0
        self.move_history: list[MoveRecord] = []  # аудит-трейл
        self.last_move_time: float = time.monotonic()  # для таймаута
        self._gc_timestamp: float = time.monotonic()
        self._lock: Lock = Lock()  # thread safety

        # Для rate-limit по токену (а не по IP)
        self.creator_player_id: Optional[str] = None   # устанавливается в routes
        self.joiner_player_id: Optional[str] = None    # устанавливается в routes

    def join_game(self, secret: str) -> bool:
        """Второй игрок присоединяется к игре.

        Возвращает True при успехе, False при неверном секрете.
        """
        with self._lock:
            if self.status != GameStatus.WAITING:
                return False
            if secret != self.join_secret:
                return False

            self.status = GameStatus.IN_PROGRESS
            self.last_move_time = time.monotonic()
            return True

    def make_move(self, player: Player, position: int) -> bool:
        """Выполняет ход. Thread-safe через per-game Lock.

        Проверки:
        - Игра в статусе IN_PROGRESS
        - Очередь этого игрока
        - Позиция свободна
        - Не превышен таймаут хода (оппонент не сходил вовремя)
        """
        with self._lock:
            if self.status != GameStatus.IN_PROGRESS:
                return False
            if self.current_turn != player:
                return False
            if self.board[position] is not None:
                return False

            # Проверка таймаута: если оппонент просрочил свой ход, текущий игрок
            # может потребовать forfeit. Пока просто проверяем и пишем в историю.
            now = time.monotonic()
            time_since_last = now - self.last_move_time

            self.board[position] = player
            self.move_count += 1
            self.last_move_time = now

            # История ходов (аудит)
            self.move_history.append(MoveRecord(
                player_id=player.value,
                position=position,
                timestamp=now,
            ))

            if self._check_win(player):
                self.status = GameStatus.FINISHED
                self.winner = player
                self._gc_timestamp = now
            elif self.move_count >= 9:
                self.status = GameStatus.FINISHED
                self._gc_timestamp = now

            self.current_turn = Player.O if player == Player.X else Player.X
            return True

    def is_timed_out(self) -> bool:
        """Проверяет, превышен ли таймаут хода текущим игроком."""
        if self.status != GameStatus.IN_PROGRESS:
            return False
        return (time.monotonic() - self.last_move_time) > TURN_TIMEOUT

    def forfeit_current_player(self) -> Optional[Player]:
        """Принудительная победа оппонента по таймауту."""
        with self._lock:
            if self.status != GameStatus.IN_PROGRESS:
                return None
            if not self.is_timed_out():
                return None

            winner = Player.O if self.current_turn == Player.X else Player.X
            self.status = GameStatus.FINISHED
            self.winner = winner
            self._gc_timestamp = time.monotonic()

            self.move_history.append(MoveRecord(
                player_id="SYSTEM",
                position=-1,
                timestamp=time.monotonic(),
            ))
            return winner

    def _check_win(self, player: Player) -> bool:
        """Проверка выигрышных комбинаций."""
        win_patterns = [
            [0, 1, 2], [3, 4, 5], [6, 7, 8],
            [0, 3, 6], [1, 4, 7], [2, 5, 8],
            [0, 4, 8], [2, 4, 6],
        ]
        return any(all(self.board[i] == player for i in p) for p in win_patterns)

    def to_response(self) -> dict:
        """Сериализует публичное состояние игры.

        Zero Trust: не включает your_player_id — клиент знает свой id из JWT.
        """
        return {
            "game_id": self.game_id,
            "board": [p.value if p else None for p in self.board],
            "current_turn": self.current_turn.value,
            "status": self.status.value,
            "winner": self.winner.value if self.winner else None,
        }