import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request

from app.models import (
    MoveRequest,
    CreateGameResponse,
    JoinRequest,
    JoinGameResponse,
    GameStateResponse,
    Player,
    GameStatus,
)
from app.game_service import TicTacToe
from app.security import TokenManager
from app.game_gc import MAX_GAMES_PER_CLIENT, MAX_TOTAL_GAMES
from app.metrics import MetricsCollector

logger = logging.getLogger(__name__)

router = APIRouter()


def _validate_token(authorization: Optional[str], tm: TokenManager) -> Optional[dict]:
    """Извлекает и проверяет Bearer-токен из заголовка Authorization."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return tm.decode_token(authorization[7:])


def _extract_token_payload(request: Request, authorization: Optional[str]):
    """Общая логика валидации токена для защищённых эндпоинтов."""
    tm: TokenManager = request.app.state.token_manager
    games: dict = request.app.state.games
    payload = _validate_token(authorization, tm)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload, games, tm


def _rate_limit(request: Request, rl_key: str, max_r: int, window: int):
    """Проверка rate-limit для конкретного tier."""
    rl = getattr(request.app.state, f"rate_limiter_{rl_key}", None)
    if rl is None:
        return
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{rl_key}"
    if not rl.check(key):
        logger.warning(f"Rate limit exceeded: {key}")
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Limit: {max_r} req/{window}s",
        )


# ─── Создание игры ──────────────────────────────────────────────────────
@router.post("/game/new", response_model=CreateGameResponse)
def new_game(request: Request):
    """Создаёт новую игру в статусе WAITING.

    Zero Trust:
    - Возвращает только ОДИН токен (для создателя, X).
    - Второй игрок получает свой токен через /game/{id}/join.
    - join_secret — одноразовый секрет для присоединения.
    """
    _rate_limit(request, "create_game", max_r=20, window=1)

    tm: TokenManager = request.app.state.token_manager
    games: dict = request.app.state.games

    # ─── Лимит по токену (а не по IP) ───
    total_games = len(games)
    if total_games >= MAX_TOTAL_GAMES:
        raise HTTPException(
            status_code=429,
            detail=f"Server at capacity ({MAX_TOTAL_GAMES} games)",
        )

    game = TicTacToe()
    games[game.game_id] = game

    token_x = tm.create_token(game.game_id, Player.X)
    game.creator_player_id = token_x  # для лимита по токену

    logger.info(f"Game created: {game.game_id} (waiting for opponent)")

    return CreateGameResponse(
        game_id=game.game_id,
        player_token=token_x,
        join_secret=game.join_secret,
    )


# ─── Присоединение к игре ──────────────────────────────────────────────
@router.post("/game/{game_id}/join", response_model=JoinGameResponse)
def join_game(game_id: str, join: JoinRequest, request: Request):
    """Второй игрок присоединяется к игре.

    Zero Trust:
    - join_secret — одноразовый пароль, создатель передаёт его второму игроку
      через side channel (чат, звонок).
    - Возвращает токен для второго игрока (O).
    - Игра переходит в статус IN_PROGRESS.
    """
    _rate_limit(request, "join_game", max_r=20, window=1)

    games: dict = request.app.state.games

    game: Optional[TicTacToe] = games.get(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    if not game.join_game(join.join_secret):
        raise HTTPException(status_code=403, detail="Invalid join secret or game already started")

    tm: TokenManager = request.app.state.token_manager
    token_o = tm.create_token(game.game_id, Player.O)
    game.joiner_player_id = token_o

    logger.info(f"Player joined game: {game_id}")

    return JoinGameResponse(player_token=token_o)


# ─── Получение состояния ──────────────────────────────────────────────
@router.get("/game/{game_id}", response_model=GameStateResponse)
def get_game_state(
    game_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Получить текущее публичное состояние игры.

    Zero Trust: не содержит your_player_id.
    """
    _rate_limit(request, "get_state", max_r=50, window=1)

    payload, games, tm = _extract_token_payload(request, authorization)

    if payload["game_id"] != game_id:
        raise HTTPException(status_code=403, detail="Token does not belong to this game")

    game: Optional[TicTacToe] = games.get(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    return GameStateResponse(**game.to_response())


# ─── Ход ────────────────────────────────────────────────────────────────
@router.post("/game/{game_id}/move", response_model=GameStateResponse)
def make_move(
    game_id: str,
    move: MoveRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Отправить ход.

    Zero Trust (6 уровней защиты + anti-spam + таймаут):
    1. Rate-limit (10 req/s — жёстче, чем для GET)
    2. Валидация токена
    3. Привязка токена к game_id (Cross-Game Attack)
    4. Анти-спуфинг (токен ≠ body)
    5. Проверка очереди хода (Out of Turn) + таймаут
    6. Игра не завершена, позиция свободна
    """
    _rate_limit(request, "move", max_r=10, window=1)

    payload, games, _ = _extract_token_payload(request, authorization)

    # ─── УРОВЕНЬ 1: Привязка токена к игре ───
    if payload["game_id"] != game_id:
        raise HTTPException(status_code=403, detail="Token does not belong to this game")

    # ─── УРОВЕНЬ 2: Анти-спуфинг ───
    token_player = Player(payload["sub"])
    if token_player != move.player_id:
        raise HTTPException(
            status_code=403,
            detail=f"Token is for player {token_player.value}, "
                   f"but move claims player {move.player_id.value}",
        )

    game: Optional[TicTacToe] = games.get(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    # ─── УРОВЕНЬ 3: Проверка очереди хода + таймаут ───
    if game.current_turn != move.player_id:
        # Проверяем, может, оппонент просрочил ход?
        if game.is_timed_out():
            game.forfeit_current_player()
            raise HTTPException(
                status_code=408,
                detail=f"Opponent timed out. {move.player_id.value} wins by forfeit",
            )
        raise HTTPException(
            status_code=403,
            detail=f"Not your turn. It is {game.current_turn.value}'s turn",
        )

    # ─── УРОВЕНЬ 4: Игра не завершена ───
    if game.status != GameStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Game is already finished")

    # ─── УРОВЕНЬ 5: Позиция свободна ───
    if game.board[move.position] is not None:
        raise HTTPException(status_code=400, detail="Position already occupied")

    # Все проверки пройдены — выполняем ход
    now = time.monotonic()
    game.make_move(move.player_id, move.position)
    response = GameStateResponse(**game.to_response())

    # ─── Метрики производительности ───
    metrics: MetricsCollector = request.app.state.metrics
    game_metrics: dict = request.app.state.game_metrics
    gid = game_id

    # Время обработки хода (уже записано middleware, но для gameplay считаем отдельно)
    move_duration = time.monotonic() - now
    metrics.record_move(move_duration)

    # Интервал между ходами (если не первый ход игры)
    if gid in game_metrics:
        prev = game_metrics[gid]["last_move_time"]
        interval = now - prev
        metrics.record_move_interval(interval)
    else:
        game_metrics[gid] = {"first_move_time": now}

    game_metrics[gid]["last_move_time"] = now

    # Длительность игры при завершении
    if game.status == GameStatus.FINISHED:
        first = game_metrics[gid].get("first_move_time", now)
        duration = now - first
        metrics.record_game_finished(duration)

    return response


# ─── Принудительная победа по таймауту ─────────────────────────────────
@router.post("/game/{game_id}/forfeit")
def forfeit_game(
    game_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Принудительное завершение игры, если оппонент превысил лимит времени."""
    _rate_limit(request, "move", max_r=10, window=1)

    payload, games, _ = _extract_token_payload(request, authorization)

    if payload["game_id"] != game_id:
        raise HTTPException(status_code=403, detail="Token does not belong to this game")

    game: Optional[TicTacToe] = games.get(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    winner = game.forfeit_current_player()
    if winner is None:
        raise HTTPException(status_code=400, detail="No timeout detected or game already over")

    # ─── Метрики: форфейт тоже завершает игру ───
    metrics: MetricsCollector = request.app.state.metrics
    game_metrics: dict = request.app.state.game_metrics
    now = time.monotonic()
    if game_id in game_metrics:
        first = game_metrics[game_id].get("first_move_time", now)
        metrics.record_game_finished(now - first)
    # Форфейт не записывается как ход, но интервал между ходами можно зафиксировать
    if game_id in game_metrics:
        prev = game_metrics[game_id].get("last_move_time", now)
        metrics.record_move_interval(now - prev)
    game_metrics[game_id] = game_metrics.get(game_id, {})
    game_metrics[game_id]["last_move_time"] = now

    return {
        "detail": f"Game over. {winner.value} wins by forfeit",
        "game_id": game_id,
        "winner": winner.value,
    }