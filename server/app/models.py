from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class Player(str, Enum):
    X = "X"
    O = "O"


class GameStatus(str, Enum):
    WAITING = "waiting"          # ожидание второго игрока
    IN_PROGRESS = "in_progress"  # игра идёт
    FINISHED = "finished"        # игра завершена


class MoveRecord(BaseModel):
    """Один ход в истории игры (аудит-трейл)."""
    player_id: str
    position: int
    timestamp: float


class MoveRequest(BaseModel):
    """Валидация входящего хода. Pydantic автоматически проверяет:
    - player_id: только "X" или "O"
    - position: целое число, 0–8 (9 клеток доски)
    """
    player_id: Player
    position: int = Field(..., ge=0, le=8, description="Позиция на доске 0–8")


class CreateGameResponse(BaseModel):
    """Ответ на создание игры — только один токен (для создателя) + join_secret.

    Zero Trust: второй игрок получает свой токен через POST /game/{id}/join.
    """
    game_id: str
    player_token: str       # токен для игрока X (создателя)
    join_secret: str        # секрет, который создатель передаёт второму игроку


class JoinRequest(BaseModel):
    """Запрос на присоединение к игре."""
    join_secret: str


class JoinGameResponse(BaseModel):
    """Ответ на присоединение — токен для второго игрока (O)."""
    player_token: str


class GameStateResponse(BaseModel):
    """Публичное состояние игры.

    Zero Trust: не содержит your_player_id — клиент знает свой id из JWT.
    """
    game_id: str
    board: list[Optional[str]]
    current_turn: str
    status: str
    winner: Optional[str] = None


class ErrorResponse(BaseModel):
    detail: str