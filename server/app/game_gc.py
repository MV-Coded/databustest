"""Фоновый сборщик завершённых игр и stale rate-limiter ключей.

Zero Trust:
- MAX_TOTAL_GAMES: ограничение общего числа игр в памяти.
- FINISHED_GAME_TTL: завершённые игры удаляются через 5 минут.
- STALLED_GAME_TIMEOUT: игры без ходов 24ч+ считаются брошенными.
- Очистка rate-limiter ключей, к которым не обращались >1 часа.
"""

import logging
import time
from threading import Thread
from typing import Dict, List, Optional

from app.game_service import TicTacToe, GameStatus, STALLED_GAME_TIMEOUT
from app.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

# Константы
FINISHED_GAME_TTL = 300.0       # 5 минут — удалить finished-игру
CLEANUP_INTERVAL = 30.0         # полный проход каждые 30 сек
MAX_TOTAL_GAMES = 1000          # максимум игр в памяти
MAX_GAMES_PER_CLIENT = 10       # лимит на одного клиента (по токену)
RATE_LIMITER_STALE_AGE = 3600.0  # 1 час — чистить rate-limiter ключи


class GameGC:
    """Поток-демон, обслуживающий хранилище игр и rate-limiters."""

    def __init__(self, games: Dict[str, TicTacToe],
                 rate_limiters: Optional[List[RateLimiter]] = None):
        self.games = games
        self.rate_limiters = rate_limiters or []
        self._stop_flag = False

    def start(self):
        thread = Thread(target=self._run, daemon=True, name="game-gc")
        thread.start()
        logger.info(
            "GameGC started (finished TTL=%ss, stalled timeout=%ss, "
            "max games=%d)",
            FINISHED_GAME_TTL, STALLED_GAME_TIMEOUT, MAX_TOTAL_GAMES,
        )

    def stop(self):
        self._stop_flag = True

    def _run(self):
        while not self._stop_flag:
            time.sleep(CLEANUP_INTERVAL)
            self._cleanup_finished()
            self._cleanup_stalled()
            self._cleanup_rate_limiters()

    def _cleanup_finished(self):
        """Удаляет завершённые игры старше TTL."""
        now = time.monotonic()
        to_delete = []
        for gid, game in list(self.games.items()):
            if game.status == GameStatus.FINISHED:
                age = now - game._gc_timestamp
                if age >= FINISHED_GAME_TTL:
                    to_delete.append(gid)

        for gid in to_delete:
            self.games.pop(gid, None)

        if to_delete:
            logger.info("GameGC: removed %d finished games", len(to_delete))

    def _cleanup_stalled(self):
        """Удаляет игры, застрявшие в IN_PROGRESS дольше STALLED_GAME_TIMEOUT.

        Предотвращает утечку памяти при брошенных играх.
        """
        now = time.monotonic()
        to_delete = []
        for gid, game in list(self.games.items()):
            if game.status == GameStatus.IN_PROGRESS:
                age = now - game.last_move_time
                if age >= STALLED_GAME_TIMEOUT:
                    to_delete.append(gid)
                    logger.warning(
                        "GameGC: stalled game %s (no moves for %.1fh) — removing",
                        gid, age / 3600,
                    )

        for gid in to_delete:
            self.games.pop(gid, None)

    def _cleanup_rate_limiters(self):
        """Очищает stale ключи из всех rate-limiters."""
        for rl in self.rate_limiters:
            before = rl.total_keys()
            rl.cleanup_stale(max_age=RATE_LIMITER_STALE_AGE)
            after = rl.total_keys()
            if before != after:
                logger.debug("RateLimiter GC: %d → %d keys", before, after)