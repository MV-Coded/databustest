"""Сборщик метрик производительности для сравнения REST API vs Pub/Sub.

Измеряет:
  - Per-endpoint latency (p50, p95, p99) в миллисекундах
  - Throughput (RPS) на эндпоинт
  - Error rate (процент не-2xx ответов)
  - Gameplay timing (длительность игры, время между ходами)
  - Uptime сервера
"""

import time
import bisect
from threading import Lock
from typing import Dict, List, Optional


class PerEndpointStats:
    """Статистика для одного эндпоинта."""
    
    __slots__ = ("count", "errors", "latencies")
    
    def __init__(self):
        self.count: int = 0
        self.errors: int = 0
        self.latencies: List[float] = []  # durations in seconds


class GameplayStats:
    """Глобальная статистика игрового процесса."""
    
    __slots__ = ("finished_games", "total_moves", "game_durations",
                 "move_intervals", "move_latencies")
    
    def __init__(self):
        self.finished_games: int = 0
        self.total_moves: int = 0
        self.game_durations: List[float] = []    # first_move → finish (сек)
        self.move_intervals: List[float] = []    # время между ходами (сек)
        self.move_latencies: List[float] = []    # серверное время обработки move


class MetricsCollector:
    """Thread-safe сборщик метрик производительности."""
    
    # Максимум точек для перцентилей (чтобы не жрать память)
    MAX_SAMPLES = 10_000

    def __init__(self):
        self._lock = Lock()
        self._start_time: float = time.monotonic()
        self._total_requests: int = 0
        self._total_errors: int = 0
        self._all_latencies: List[float] = []  # глобальный (для aggregate)
        # Per-endpoint
        self._endpoints: Dict[str, PerEndpointStats] = {}
        # Throughput: окно в 60 секунд для RPS
        self._recent_timestamps: List[float] = []
        # Gameplay
        self._gameplay = GameplayStats()

    # ─── Request tracking ─────────────────────────────────────────────

    def record_request(self, endpoint: str, duration_s: float,
                       status_code: int) -> None:
        """Записать один HTTP-запрос."""
        with self._lock:
            self._total_requests += 1
            if status_code >= 400:
                self._total_errors += 1

            # Глобальная latency
            self._all_latencies.append(duration_s)
            if len(self._all_latencies) > self.MAX_SAMPLES:
                self._all_latencies = self._all_latencies[-self.MAX_SAMPLES:]

            # Per-endpoint
            ep = self._endpoints.get(endpoint)
            if ep is None:
                ep = PerEndpointStats()
                self._endpoints[endpoint] = ep
            ep.count += 1
            if status_code >= 400:
                ep.errors += 1
            ep.latencies.append(duration_s)
            if len(ep.latencies) > self.MAX_SAMPLES:
                ep.latencies = ep.latencies[-self.MAX_SAMPLES:]

            # Throughput window
            now = time.monotonic()
            self._recent_timestamps.append(now)
            cutoff = now - 60.0
            while self._recent_timestamps and self._recent_timestamps[0] < cutoff:
                self._recent_timestamps.pop(0)

    # ─── Gameplay tracking ────────────────────────────────────────────

    def record_move(self, server_time_s: float) -> None:
        """Записать серверное время обработки одного хода."""
        with self._lock:
            self._gameplay.total_moves += 1
            self._gameplay.move_latencies.append(server_time_s)
            if len(self._gameplay.move_latencies) > self.MAX_SAMPLES:
                self._gameplay.move_latencies = \
                    self._gameplay.move_latencies[-self.MAX_SAMPLES:]

    def record_move_interval(self, interval_s: float) -> None:
        """Записать время между двумя ходами (от серверного timestamp)."""
        with self._lock:
            self._gameplay.move_intervals.append(interval_s)
            if len(self._gameplay.move_intervals) > self.MAX_SAMPLES:
                self._gameplay.move_intervals = \
                    self._gameplay.move_intervals[-self.MAX_SAMPLES:]

    def record_game_finished(self, duration_s: float) -> None:
        """Записать завершённую игру (время от первого хода до финиша)."""
        with self._lock:
            self._gameplay.finished_games += 1
            self._gameplay.game_durations.append(duration_s)
            if len(self._gameplay.game_durations) > self.MAX_SAMPLES:
                self._gameplay.game_durations = \
                    self._gameplay.game_durations[-self.MAX_SAMPLES:]

    # ─── Вычисление перцентилей ──────────────────────────────────────

    @staticmethod
    def _percentiles(data: List[float],
                     ps: tuple = (50, 95, 99)) -> Dict[str, float]:
        """Вычисляет p50/p95/p99 из отсортированного списка (в ms)."""
        if not data:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        # На месте не сортируем — копируем
        s = sorted(data)
        n = len(s)
        result = {}
        for p in ps:
            idx = max(0, min(n - 1, int(n * p / 100)))
            result[f"p{p}"] = round(s[idx] * 1000, 2)  # ms
        return result

    # ─── Снапшот (для /admin/metrics) ────────────────────────────────

    def snapshot(self) -> dict:
        """Возвращает полный срез метрик для экспорта."""
        with self._lock:
            now = time.monotonic()
            uptime = now - self._start_time
            window_60s = sum(
                1 for t in self._recent_timestamps
                if t > now - 60.0
            )

            # Per-endpoint
            endpoints = {}
            for name, ep in sorted(self._endpoints.items()):
                ep_lat = self._percentiles(ep.latencies)
                # Throughput per endpoint за последние 60s (грубо)
                ep_rps = round(ep.count / max(uptime, 1), 2)
                endpoints[name] = {
                    "count": ep.count,
                    "errors": ep.errors,
                    "error_rate_pct": round(
                        (ep.errors / max(ep.count, 1)) * 100, 2
                    ),
                    "latency_ms": ep_lat,
                    "throughput_rps": ep_rps,
                }

            # Глобальная latency
            global_lat = self._percentiles(self._all_latencies)

            # Gameplay
            gp = self._gameplay
            move_lat = self._percentiles(gp.move_latencies)
            interval_lat = self._percentiles(gp.move_intervals)
            game_dur = self._percentiles(gp.game_durations)

            return {
                "server": {
                    "uptime_seconds": round(uptime, 1),
                    "total_requests": self._total_requests,
                    "total_errors": self._total_errors,
                    "error_rate_pct": round(
                        (self._total_errors / max(self._total_requests, 1)) * 100, 2
                    ),
                    "throughput_60s_rps": round(window_60s / 60, 2),
                    "global_latency_ms": global_lat,
                },
                "endpoints": endpoints,
                "gameplay": {
                    "total_games_finished": gp.finished_games,
                    "total_moves": gp.total_moves,
                    "avg_moves_per_game": round(
                        gp.total_moves / max(gp.finished_games, 1), 1
                    ),
                    "move_processing_ms": move_lat,
                    "move_intervals_s": {
                        k: round(v / 1000, 3)
                        for k, v in interval_lat.items()
                    } if interval_lat else {"p50": 0, "p95": 0, "p99": 0},
                    "game_duration_s": game_dur,
                },
            }