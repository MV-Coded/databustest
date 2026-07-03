# Zero Trust Game Bus

**Безопасная шина данных для многопользовательских игр с архитектурой Zero Trust.**

Сервер REST API на FastAPI с многоуровневой защитой: JWT-аутентификация с отзывом токенов, rate-limiting, сетевая изоляция Docker, принудительная валидация ходов и аудит-трейл.

**Цель проекта:** сравнение производительности REST API и Redis Pub/Sub как способов передачи данных в многопользовательской игре. Для этого встроены метрики latency (p50/p95/p99), throughput и gameplay timing на каждый эндпоинт.

**Это игра?** Да — игра в крестики-нолики с полной логикой: проверка победителя, очерёдность ходов, таймауты, ничьи. **Это шина данных?** Тоже да — каждый ход проходит 6 уровней валидации, никакому клиенту нельзя доверять.

---

## Архитектура

```
┌─ player_x_net (bridge) ─┐   ┌─ player_o_net (bridge) ─┐
│  Player X  ───┐          │   │  Player O  ───┐          │
│  Attacker ────┤          │   │               │          │
└───────────────┼──────────┘   └───────────────┼──────────┘
                │                              │
                ▼                              ▼
        ┌──────────────────────────────────────────┐
        │         FastAPI Server (:8000)            │
        │  ┌─────────┐ ┌──────────┐ ┌───────────┐ │
        │  │  JWT +   │ │  Game    │ │ Rate      │ │
        │  │ Blacklist│ │  Engine  │ │ Limiter   │ │
        │  └─────────┘ └──────────┘ └───────────┘ │
        │  ┌────────────────────────────────────┐ │
        │  │ GameGC (cleanup + garbage collect) │ │
        │  └────────────────────────────────────┘ │
        └──────────────────────────────────────────┘
                        │
              ┌─────────┼─────────┐
              │         │         │
       ┌──────┴─┐ ┌────┴───┐ ┌───┴──────┐
       │admin_net│ │player_x│ │player_o  │
       │(bridge) │ │_net    │ │_net      │
       └────────┘ └────────┘ └──────────┘
```

### Принципы Zero Trust

| Принцип | Реализация |
|---|---|
| **Никакому клиенту нельзя доверять** | Каждый запрос проходит 6 уровней валидации + rate-limiting |
| **Минимальные привилегии** | Игрок получает токен только для своей роли (X или O) |
| **Сетевая изоляция** | Каждый игрок в своей bridge-сети, не видит других игроков |
| **Сервер — источник истины** | Доска только на сервере, клиент получает snapshot |
| **Неотзываемые токены** | JWT с `jti` + blacklist для принудительного отзыва |
| **Аудит** | История ходов с timestamp для доказательства честности |

---

## Установка

### Системные требования

- **Docker** ≥ 24.x + **Docker Compose** ≥ 2.x
- **Python** 3.11+ (только для локального запуска тестов)
- **curl** + **jq** (для ручных тестов)

### Ubuntu 22.04 / 24.04 LTS

```bash
# 1. Установка Docker
sudo apt update && sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update && sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-compose-plugin curl jq

# 2. Добавить пользователя в группу docker (чтобы не использовать sudo)
sudo usermod -aG docker $USER
newgrp docker  # или выйти и зайти снова

# 3. Проверить установку
docker compose version
docker info --format '{{.ServerVersion}}'
```

### Arch Linux

```bash
# 1. Установка Docker
sudo pacman -Syu docker docker-compose curl jq

# 2. Запуск и автозагрузка Docker
sudo systemctl enable --now docker

# 3. Добавить пользователя в группу docker
sudo usermod -aG docker $USER
newgrp docker  # или выйти и зайти снова

# 4. Проверить установку
docker compose version
docker info --format '{{.ServerVersion}}'
```

### Клонирование проекта

```bash
git clone https://github.com/MV-Coded/databustest databustest
cd databustest
```

---

## Запуск

### Быстрый старт (одной командой)

```bash
./run_all.sh
```

Скрипт автоматически:
1. Собирает Docker-образ сервера
2. Запускает контейнеры (server, player_x, player_o, attacker)
3. Ждёт готовности сервера (healthcheck)
4. Запускает Red Team тесты безопасности
5. Проверяет сетевую изоляцию

### Пошаговый запуск

```bash
# 1. Сборка сервера
docker compose build server

# 2. Запуск всех контейнеров
docker compose up -d

# 3. Проверка здоровья
curl -sf http://localhost:8000/health

# 4. Просмотр метрик
curl -sf http://localhost:8000/admin/metrics | jq .
```

### Ручной тест полного игрового цикла

Скопируйте и выполните:

```bash
# Создание игры
CREATE=$(curl -s -X POST http://localhost:8000/game/new)
GID=$(echo "$CREATE" | jq -r '.game_id')
TX=$(echo "$CREATE" | jq -r '.player_token')
JS=$(echo "$CREATE" | jq -r '.join_secret')

echo "Game ID: $GID"
echo "X token: ${TX:0:20}..."
echo "Join secret: $JS"

# Второй игрок присоединяется
JOIN=$(curl -s -X POST "http://localhost:8000/game/$GID/join" \
  -H "Content-Type: application/json" -d "{\"join_secret\":\"$JS\"}")
TO=$(echo "$JOIN" | jq -r '.player_token')
echo "O token: ${TO:0:20}..."

# X ходит
curl -s -X POST "http://localhost:8000/game/$GID/move" \
  -H "Authorization: Bearer $TX" \
  -H "Content-Type: application/json" \
  -d '{"player_id":"X","position":4}' | jq .

# O отвечает
curl -s -X POST "http://localhost:8000/game/$GID/move" \
  -H "Authorization: Bearer $TO" \
  -H "Content-Type: application/json" \
  -d '{"player_id":"O","position":0}' | jq .
```

---

## API Reference

### `GET /health`
Публичный эндпоинт. Проверка работоспособности сервера.

```bash
curl http://localhost:8000/health
# → {"status": "ok"}
```

### `POST /game/new`
Создание новой игры. Возвращает **один** токен (для создателя) и join_secret.

```bash
curl -s -X POST http://localhost:8000/game/new | jq .
# → {
#     "game_id": "uuid-...",
#     "player_token": "eyJhbG...",   ← только X
#     "join_secret": "uuid-..."      ← секрет для второго игрока
#   }
```

**Zero Trust**: второй игрок получает свой токен через `/join`, не через `/new`.

### `POST /game/{id}/join`
Присоединение второго игрока. Требует `join_secret`.

```bash
curl -s -X POST http://localhost:8000/game/{id}/join \
  -H "Content-Type: application/json" \
  -d '{"join_secret":"uuid-..."}' | jq .
# → {"player_token": "eyJhbG..."}  ← токен для O
```

### `GET /game/{id}`
Получение состояния игры. Требует `Authorization: Bearer <token>`.

```bash
curl -s http://localhost:8000/game/{id} \
  -H "Authorization: Bearer eyJhbG..." | jq .
# → {
#     "game_id": "uuid-...",
#     "board": ["X", null, null, null, "O", null, null, null, null],
#     "current_turn": "X",
#     "status": "in_progress",
#     "winner": null
#   }
```

**Zero Trust**: ответ не содержит `your_player_id` — клиент знает свою роль из JWT.

### `POST /game/{id}/move`
Отправка хода. **6 уровней защиты** (см. таблицу ниже).

```bash
curl -s -X POST http://localhost:8000/game/{id}/move \
  -H "Authorization: Bearer eyJhbG..." \
  -H "Content-Type: application/json" \
  -d '{"player_id":"X","position":4}' | jq .
```

### `POST /game/{id}/forfeit`
Принудительная победа, если оппонент превысил таймаут (5 минут).

```bash
curl -s -X POST http://localhost:8000/game/{id}/forfeit \
  -H "Authorization: Bearer eyJhbG..." | jq .
# → {"detail": "Game over. X wins by forfeit", ...}
```

### `GET /admin/metrics`
Полный срез метрик производительности и состояния сервера.
Используется для сравнения REST API vs Redis Pub/Sub.

```bash
curl -sf http://localhost:8000/admin/metrics | jq .
# → {
#     "server": {
#       "uptime_seconds": 340.6,
#       "total_requests": 144,
#       "total_errors": 15,
#       "error_rate_pct": 10.42,
#       "throughput_60s_rps": 0.35,
#       "global_latency_ms": {
#         "p50": 0.35,      # медианный запрос: 0.35ms
#         "p95": 1.01,      # 95% запросов быстрее 1ms
#         "p99": 1.48       # 99% запросов быстрее 1.5ms
#       }
#     },
#     "endpoints": {
#       "POST /game/new": {
#         "count": 50, "errors": 0, "error_rate_pct": 0.0,
#         "latency_ms": {"p50": 0.8, "p95": 1.2, "p99": 2.1},
#         "throughput_rps": 0.15
#       },
#       "POST /game/{id}/join": {
#         "count": 30, "errors": 0, "error_rate_pct": 0.0,
#         "latency_ms": {"p50": 1.29, "p95": 1.48, "p99": 1.48},
#         "throughput_rps": 0.09
#       },
#       "POST /game/{id}/move": {
#         "count": 100, "errors": 20, "error_rate_pct": 20.0,
#         "latency_ms": {"p50": 0.89, "p95": 3.43, "p99": 3.43},
#         "throughput_rps": 0.3
#       },
#       "GET /game/{id}": {
#         "count": 200, "errors": 10, "error_rate_pct": 5.0,
#         "latency_ms": {"p50": 0.48, "p95": 0.76, "p99": 0.76},
#         "throughput_rps": 0.6
#       }
#     },
#     "gameplay": {
#       "total_games_finished": 3,
#       "total_moves": 15,
#       "avg_moves_per_game": 5.0,
#       "move_processing_ms": {"p50": 0.04, "p95": 0.05, "p99": 0.06},
#       "move_intervals_s": {"p50": 0.008, "p95": 0.01, "p99": 0.02},
#       "game_duration_s": {"p50": 0.0, "p95": 0.0, "p99": 0.0}
#     },
#     "games_state": {
#       "total": 30, "waiting": 5, "in_progress": 20, "finished": 5
#     },
#     "security": {"blacklist_size": 0}
#   }
```

### Уровни защиты эндпоинта `/move`

| Уровень | Проверка | Код | Атака |
|---|---|---|---|
| 0 | Rate-limit (10 req/s) | `429` | DoS / спам |
| 1 | Валидация JWT | `401` | Фальшивый токен |
| 2 | Привязка к `game_id` | `403` | Cross-Game Attack |
| 3 | Anti-spoofing (токен ≠ body) | `403` | Replay / Spoofing |
| 4 | Очередь хода + таймаут | `403` / `408` | Out of Turn / затягивание |
| 5 | Статус игры | `400` | Двойной ход в finished |
| 6 | Позиция свободна | `400` | Double-move |
| — | Pydantic schema (ge=0, le=8) | `422` | Выход за границы доски |

---

## Метрики производительности

Система собирает per-endpoint статистику latency (p50/p95/p99), throughput и error rate — это нужно для сравнения REST API vs Redis Pub/Sub в рамках исследования производительности двух архитектур.

### Какие метрики собираются

| Метрика | Ед. изм. | Где | Описание |
|---|---|---|---|
| `global_latency_ms` | ms | `server` | Latency всех запросов (p50/p95/p99) |
| `endpoints.*.latency_ms` | ms | `endpoints` | Per-endpoint latency |
| `endpoints.*.throughput_rps` | req/s | `endpoints` | Средний RPS эндпоинта за всё время |
| `endpoints.*.error_rate_pct` | % | `endpoints` | Процент ошибок (4xx/5xx) |
| `server.throughput_60s_rps` | req/s | `server` | RPS за последние 60 секунд |
| `gameplay.move_processing_ms` | ms | `gameplay` | Чистое время выполнения хода на сервере (без middleware) |
| `gameplay.move_intervals_s` | s | `gameplay` | Время между последовательными ходами |
| `gameplay.game_duration_s` | s | `gameplay` | Длительность игры от первого хода до финиша |

### Как замеряется latency

FastAPI timing middleware перехватывает каждый HTTP-запрос и замеряет полное время «от входа в приложение до отправки ответа»:

```
       │ middleware: start timer      middleware: stop timer + record
       ▼                              ▼
request ──→ rate-limit → auth → validation → game logic → response
       ↑                                                      │
       └────────────────── duration ──────────────────────────┘
```

Дополнительно внутри `POST /game/{id}/move` замеряется чистое время обработки хода (без учёта rate-limit, middleware и auth) — это `gameplay.move_processing_ms`.

### Пример вывода

```bash
curl -sf http://localhost:8000/admin/metrics | jq '.server, .endpoints, .gameplay'
```

```json
{
  "uptime_seconds": 340.6,
  "total_requests": 144,
  "total_errors": 15,
  "error_rate_pct": 10.42,
  "throughput_60s_rps": 0.35,
  "global_latency_ms": {"p50": 0.35, "p95": 1.01, "p99": 1.48}
}
```

**Типичные значения (в Docker bridge-сети, локальный запуск):**

| Эндпоинт | p50 | p95 | p99 |
|---|---|---|---|
| `POST /game/new` | 0.8 ms | 1.2 ms | 2.1 ms |
| `POST /game/{id}/join` | 1.3 ms | 1.5 ms | 1.5 ms |
| `POST /game/{id}/move` | 0.9 ms | 3.4 ms | 3.4 ms |
| `GET /game/{id}` | 0.5 ms | 0.8 ms | 0.8 ms |
| `GET /health` | 0.3 ms | 0.6 ms | 0.7 ms |

### Генерация эталонных замеров

```bash
# Быстрый тест: создать N игр, сыграть до конца, снять метрики
for i in $(seq 1 5); do
  GAME=$(curl -s -X POST http://localhost:8000/game/new)
  GID=$(echo "$GAME" | jq -r '.game_id')
  TX=$(echo "$GAME" | jq -r '.player_token')
  JS=$(echo "$GAME" | jq -r '.join_secret')
  TO=$(curl -s -X POST "http://localhost:8000/game/$GID/join" \
    -H "Content-Type: application/json" -d "{\"join_secret\":\"$JS\"}" | jq -r '.player_token')

  # Быстрая партия (5 ходов на победу X)
  curl -s -X POST "http://localhost:8000/game/$GID/move" -H "Authorization: Bearer $TX" \
    -H "Content-Type: application/json" -d '{"player_id":"X","position":4}' > /dev/null
  curl -s -X POST "http://localhost:8000/game/$GID/move" -H "Authorization: Bearer $TO" \
    -H "Content-Type: application/json" -d '{"player_id":"O","position":0}' > /dev/null
  curl -s -X POST "http://localhost:8000/game/$GID/move" -H "Authorization: Bearer $TX" \
    -H "Content-Type: application/json" -d '{"player_id":"X","position":1}' > /dev/null
  curl -s -X POST "http://localhost:8000/game/$GID/move" -H "Authorization: Bearer $TO" \
    -H "Content-Type: application/json" -d '{"player_id":"O","position":3}' > /dev/null
  curl -s -X POST "http://localhost:8000/game/$GID/move" -H "Authorization: Bearer $TX" \
    -H "Content-Type: application/json" -d '{"player_id":"X","position":2}' > /dev/null
done

# Посмотреть результат
curl -sf http://localhost:8000/admin/metrics | jq '.gameplay'
```

---

## Тестирование безопасности

### Red Team атаки

```bash
# Из контейнера attacker
docker compose exec attacker sh /tests/run_attacks.sh
```

Проверяемые сценарии:

| Тест | Атака | Ожидаемый код |
|---|---|---|
| 0 | Smoke test (create + join + health) | `200` / `403` |
| 1 | Happy path (X → O → X) | `200` |
| 2 | **Spoofing** (токен X, тело O) | `403` |
| 3 | **Out of Turn** (X ходит дважды) | `403` |
| 4 | Happy path (O отвечает) | `200` |
| 5 | **Cross-Game** (токен Game1 → Game2) | `403` |
| 6 | **Без токена** | `401` |
| 7 | **Фальшивый JWT** | `401` |
| 8 | **Позиция вне доски** (99) | `422` |
| 9 | **Занятая клетка** | `400` |
| 10 | **Replay join_secret** | `403` |

### Сетевая изоляция

```bash
# Из контейнера player X
docker compose exec player_x sh /tests/network_isolation.sh
```

| Проверка | Ожидание |
|---|---|
| Player X → Server | Доступен |
| Player X → Player O | **Заблокирован** (разные bridge-сети) |
| Player X → Admin net | **Заблокирован** (admin_net изолирована) |

### Доказательство сетевой изоляции

```bash
docker network inspect databustest_player_x_net \
  -f '{{range .Containers}}{{.Name}} {{end}}'
# → databustest-server-1 databustest-player_x-1 databustest-attacker-1

docker network inspect databustest_player_o_net \
  -f '{{range .Containers}}{{.Name}} {{end}}'
# → databustest-server-1 databustest-player_o-1
#   (player_o НЕ ВИДИТ player_x — разные сети)
```

---

## Остановка

```bash
# Остановить все контейнеры (сохранить данные)
docker compose stop

# Остановить и удалить контейнеры + сети
docker compose down

# Полная очистка (контейнеры + сети + volume)
docker compose down -v
```

---

## Деинсталляция

### Ubuntu

```bash
# Удалить проект
rm -rf ~/databustest

# Удалить Docker (опционально)
sudo apt purge -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo rm -rf /var/lib/docker /etc/docker
sudo rm -f /etc/apt/keyrings/docker.asc
sudo rm -f /etc/apt/sources.list.d/docker.list

# Удалить curl jq (опционально)
sudo apt purge -y curl jq
sudo apt autoremove -y
```

### Arch Linux

```bash
# Удалить проект
rm -rf ~/databustest

# Удалить Docker (опционально)
sudo pacman -Rns docker docker-compose
sudo rm -rf /var/lib/docker /etc/docker

# Удалить curl jq (опционально)
sudo pacman -Rns curl jq
```

---

## Структура проекта

```
databustest/
├── server/                     # Серверное приложение (FastAPI)
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py             # Точка входа, lifespan, rate-limiters
│   │   ├── config.py           # Pydantic Settings (JWT secret, algorithm)
│   │   ├── models.py           # Pydantic схемы (MoveRequest, GameState, etc.)
│   │   ├── security.py         # TokenManager (JWT + jti + blacklist)
│   │   ├── game_service.py     # TicTacToe — ядро игры (Lock, timeout, history)
│   │   ├── routes.py           # FastAPI routes (6 уровней валидации)
│   │   ├── ratelimit.py        # Sliding-window rate limiter
│   │   ├── metrics.py          # MetricsCollector (latency, throughput, gameplay timing)
│   │   └── game_gc.py          # GameGC (cleanup finished/stalled games)
│   ├── requirements.txt
│   └── Dockerfile
├── tests/
│   ├── run_attacks.sh          # Red Team: 14 тестов безопасности
│   └── network_isolation.sh    # Проверка Docker network isolation
├── docker-compose.yml          # 3 bridge-сети, 4 контейнера
├── run_all.sh                  # Оркестратор: сборка → запуск → тесты
└── README.md                   # Этот файл
```

---

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `JWT_SECRET` | `zero-trust-game-bus-secret-2026` | Секретный ключ для подписи JWT |
| `JWT_ALGORITHM` | `HS256` | Алгоритм подписи |
| `JWT_EXPIRE_SECONDS` | `86400` | Время жизни токена (24 часа) |

Переопределяются в `docker-compose.yml` → `environment` или через `.env` файл.

---

## Конфигурация защиты

Параметры в коде (настраиваются в `game_gc.py`, `game_service.py`, `main.py`):

| Параметр | Значение | Описание |
|---|---|---|
| `MAX_TOTAL_GAMES` | 1000 | Максимум игр в памяти |
| `MAX_GAMES_PER_CLIENT` | 10 | Лимит игр на один IP (Docker bridge) |
| `FINISHED_GAME_TTL` | 300 с | Жизнь finished-игры до удаления GC |
| `STALLED_GAME_TIMEOUT` | 86400 с (24ч) | Брошенные игры удаляются через сутки |
| `TURN_TIMEOUT` | 300 с (5 мин) | Таймаут хода (forfeit) |
| Rate-limit move | 10 req/s | `/move` |
| Rate-limit create | 20 req/s | `/game/new` |
| Rate-limit join | 20 req/s | `/game/{id}/join` |
| Rate-limit get_state | 50 req/s | `GET /game/{id}` |

---

## ИИ

Сделано с помощью искуственного интеллекта
