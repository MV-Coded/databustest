import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import Settings
from app.security import TokenManager
from app.ratelimit import RateLimiter
from app.game_gc import GameGC
from app.metrics import MetricsCollector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация сервера: JWT, хранилище, rate-limiters, GC, метрики."""
    # ─── Токены ───
    app.state.token_manager = TokenManager(
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expire_seconds=settings.jwt_expire_seconds,
    )

    # ─── Хранилище игр ───
    app.state.games = {}
    app.state.game_metrics = {}  # per-game: first_move_time, last_move_time

    # ─── Метрики производительности ───
    app.state.metrics = MetricsCollector()

    # ─── Rate-limiters ───
    app.state.rate_limiter_move = RateLimiter(max_requests=10, window_seconds=1)
    app.state.rate_limiter_create_game = RateLimiter(max_requests=20, window_seconds=1)
    app.state.rate_limiter_join_game = RateLimiter(max_requests=20, window_seconds=1)
    app.state.rate_limiter_get_state = RateLimiter(max_requests=50, window_seconds=1)

    # ─── GC + rate-limiter cleanup ───
    rls = [
        app.state.rate_limiter_move,
        app.state.rate_limiter_create_game,
        app.state.rate_limiter_join_game,
        app.state.rate_limiter_get_state,
    ]
    game_gc = GameGC(app.state.games, rls)
    game_gc.start()
    app.state.game_gc = game_gc

    logger.info(
        "Server initialized: JWT+JTI, rate-limiters, MetricsCollector, "
        f"GameGC (TTL={settings.jwt_expire_seconds}s)"
    )

    yield
    if hasattr(app.state, "game_gc"):
        app.state.game_gc.stop()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="Zero Trust Game Bus",
    description="Безопасная шина данных для многопользовательских игр. "
                "Zero Trust: ни один клиент не заслуживает доверия. "
                "Встроенные метрики производительности для сравнения REST vs Pub/Sub.",
    version="0.4.0",
    lifespan=lifespan,
)


# ─── Timing middleware ──────────────────────────────────────────────────
@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """Замеряет время каждого HTTP-запроса и записывает в метрики.

    Измеряет полное время «от входа в приложение до отправки ответа»,
    включая все проверки (auth, rate-limit, game logic).
    """
    start = time.monotonic()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration = time.monotonic() - start
        metrics: MetricsCollector = request.app.state.metrics
        path = request.url.path
        for segment in path.split("/"):
            if len(segment) == 36 and segment.count("-") == 4:
                path = path.replace(segment, "{id}", 1)
        method = request.method
        label = f"{method} {path}"
        status = getattr(response, "status_code", 500) if response else 500
        metrics.record_request(label, duration, status)


from app.routes import router  # noqa: E402

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}


# ─── Admin: расширенные метрики ─────────────────────────────────────────
@app.get("/admin/metrics")
def admin_metrics(request: Request):
    """Полный срез метрик производительности и состояния сервера.

    Используется для сравнения REST API vs Redis Pub/Sub:
    - latency (p50/p95/p99) на каждый эндпоинт
    - throughput (RPS)
    - error rate
    - gameplay timing (длительность хода, игры)
    """
    metrics: MetricsCollector = request.app.state.metrics
    games: dict = request.app.state.games
    tm: TokenManager = request.app.state.token_manager

    snapshot = metrics.snapshot()

    # Добавляем состояние игр
    waiting = sum(1 for g in games.values() if g.status == "waiting")
    in_progress = sum(1 for g in games.values() if g.status == "in_progress")
    finished = sum(1 for g in games.values() if g.status == "finished")

    snapshot["games_state"] = {
        "total": len(games),
        "waiting": waiting,
        "in_progress": in_progress,
        "finished": finished,
    }
    snapshot["security"] = {
        "blacklist_size": tm.blacklist_size(),
    }

    return snapshot