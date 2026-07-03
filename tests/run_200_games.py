#!/usr/bin/env python3
"""200 RANDOM-игр. Каждый ход — случайная свободная клетка.
Оптимизировано: минимум HTTP-запросов, ускорено в 2x.
"""
import json, os, random, sys, time, urllib.request, urllib.error

BASE = os.environ.get("SERVER_URL", "http://localhost:8000")
GAMES = 200
MOVE_INTERVAL = 0.11  # 110ms (чуть выше 100ms = 10 req/s limit)
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "metrics_200_games.json")

_last = [0.0]

def http(method, path, headers=None, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if headers:
        for k, v in headers.items(): req.add_header(k, v)
    if body: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 0, {"error": str(e)}

def throttle(min_int):
    now = time.monotonic()
    gap = now - _last[0]
    if gap < min_int:
        time.sleep(min_int - gap)
    _last[0] = time.monotonic()

def empty_cells(board):
    return [i for i, c in enumerate(board) if c is None]

def simulate_game():
    """Одна RANDOM игра. Возвращает число ходов или None при ошибке."""
    throttle(0.08)
    s, d = http("POST", "/game/new")
    if s != 200: return None, str(d)
    gid, tx, js = d["game_id"], d["player_token"], d["join_secret"]

    throttle(0.08)
    s, d = http("POST", f"/game/{gid}/join", body={"join_secret": js})
    if s != 200: return None, f"join {s}"
    to = d["player_token"]

    tokens = {"X": tx, "O": to}
    board = [None] * 9
    turn = "X"
    moves = 0

    while moves < 9:
        cells = empty_cells(board)
        if not cells:
            break
        pos = random.choice(cells)
        token = tokens[turn]
        throttle(MOVE_INTERVAL)
        s, d = http("POST", f"/game/{gid}/move",
                    {"Authorization": f"Bearer {token}"},
                    {"player_id": turn, "position": pos})
        if s != 200 and s != 408:
            return moves, f"move {s}"
        if s == 200:
            board = d.get("board", board)
            if d.get("status") == "finished":
                return moves + 1, None
            turn = d.get("current_turn", "O" if turn == "X" else "X")
        moves += 1

    return moves, "draw/unknown"

# ─── RUN ───
print("=" * 60)
print(f"  200 RANDOM-IGR (throttle={MOVE_INTERVAL*1000:.0f}ms)")
print("=" * 60)

played = 0
errors = 0
moves_total = 0
start = time.monotonic()

while played < GAMES:
    m, err = simulate_game()
    if m is None:
        errors += 1
        if errors > 50:
            print(f"\n  ERROR: too many failures (last: {err})")
            break
        time.sleep(0.3)
        continue
    played += 1
    moves_total += m

    if played % 20 == 0:
        elapsed = time.monotonic() - start
        rate = played / elapsed if elapsed > 0 else 0
        print(f"\r  {played:3d}/{GAMES}  |  {elapsed:5.0f}s  |  "
              f"{rate:.2f} игр/с  |  ср.ходов: {moves_total/played:.1f}  ", end="")

elapsed = time.monotonic() - start
print(f"\n  Готово: {played} игр за {elapsed:.0f}s "
      f"({played/elapsed:.2f} игр/с), ср. {moves_total/played:.1f} ходов")

# ─── Metrics ───
print("  Сбор финальных метрик...")
s, d = http("GET", "/admin/metrics")
if s != 200:
    print(f"  FAIL: metrics endpoint returned {s}")
    sys.exit(1)

out = {
    "simulation": {
        "games_attempted": GAMES,
        "games_played": played,
        "errors": errors,
        "total_moves_simulated": moves_total,
        "avg_moves_per_game": round(moves_total / played, 1),
        "elapsed_seconds": round(elapsed, 1),
        "games_per_second": round(played / elapsed, 3),
        "move_interval_throttle_ms": MOVE_INTERVAL * 1000,
    },
    "metrics": d,
}

with open(OUTPUT, "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

# ─── Print ───
print("\n" + "=" * 60)
print("  ИТОГОВЫЕ МЕТРИКИ (RANDOM strategy)")
print("=" * 60)

sv = d.get("server", {}); gl = sv.get("global_latency_ms", {})
gp = d.get("gameplay", {}); mp = gp.get("move_processing_ms", {})
mi = gp.get("move_intervals_s", {}); gd = gp.get("game_duration_s", {})
eps = d.get("endpoints", {}); gs = d.get("games_state", {})

print(f"\n  Сервер:")
print(f"    Uptime:  {sv.get('uptime_seconds', 0):.0f}s")
print(f"    Reqs:    {sv.get('total_requests', 0)}  "
      f"Errors: {sv.get('total_errors', 0)} "
      f"({sv.get('error_rate_pct', 0)}%)")
print(f"    RPS(60s): {sv.get('throughput_60s_rps', 0)}")
print(f"    Latency:  p50={gl.get('p50')}ms  "
      f"p95={gl.get('p95')}ms  p99={gl.get('p99')}ms")

print(f"\n  Геймплей:")
print(f"    Игр завершено: {gp.get('total_games_finished', 0)}")
print(f"    Всего ходов:   {gp.get('total_moves', 0)}")
print(f"    Среднее ходов: {gp.get('avg_moves_per_game', 0)}")
print(f"    Обработка хода сервером: p50={mp.get('p50')}ms  "
      f"p95={mp.get('p95')}ms  p99={mp.get('p99')}ms")
print(f"    Интервал между ходами:   p50={mi.get('p50')}s  "
      f"p95={mi.get('p95')}s  p99={mi.get('p99')}s")
print(f"    Длительность игры:       p50={gd.get('p50')}s  "
      f"p95={gd.get('p95')}s  p99={gd.get('p99')}s")

print(f"\n  Per-endpoint:")
for name, ep in sorted(eps.items()):
    lat = ep.get("latency_ms", {})
    print(f"    {name:30s}  cnt={ep['count']:4d}  "
          f"err={ep['errors']:3d}  "
          f"p50={lat.get('p50','?'):>5}ms  "
          f"p95={lat.get('p95','?'):>5}ms  "
          f"RPS={ep.get('throughput_rps', 0):.2f}")

print(f"\n  State:  total={gs.get('total')}  "
      f"(wait={gs.get('waiting')}  "
      f"ip={gs.get('in_progress')}  "
      f"fin={gs.get('finished')})  "
      f"blacklist={d.get('security', {}).get('blacklist_size', 0)}")

print(f"\n  Сохранено: {OUTPUT}")
print("=" * 60)