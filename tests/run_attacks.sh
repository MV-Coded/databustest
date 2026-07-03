#!/bin/sh
# =============================================================================
# RED TEAM: Комплексный тест безопасности игровой шины данных
# Zero Trust: проверка create → join → move последовательности
# =============================================================================
# Запуск: docker compose exec attacker sh /tests/run_attacks.sh
# =============================================================================

SERVER_URL="http://server:8000"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
PASS=0; FAIL=0

check_status() {
    local desc="$1" exp="$2" act="$3" body="$4"
    if [ "$act" = "$exp" ]; then
        echo "${GREEN}[PASS]${NC} $desc (HTTP $act)"
        PASS=$((PASS + 1))
    else
        echo "${RED}[FAIL]${NC} $desc"
        echo "  Ожидали: $exp, Получили: $act"
        echo "  Тело: $body"
        FAIL=$((FAIL + 1))
    fi
}

curljson() {
    # usage: curljson METHOD URL [BODY_FILE]
    local method="$1" url="$2" body="${3:--}"
    if [ "$body" = "-" ]; then
        curl -s -X "$method" "$url"
    else
        echo "$body" | curl -s -X "$method" "$url" -H "Content-Type: application/json" -d @-
    fi
}

echo "=============================================="
echo "  RED TEAM: АТАКИ НА ИГРОВУЮ ШИНУ ДАННЫХ"
echo "=============================================="
echo ""

# ─── Подготовка ───
echo "${YELLOW}[SETUP]${NC} Создаём Игру №1..."
GAME1_RESP=$(curl -s -X POST "$SERVER_URL/game/new")
GAME1_ID=$(echo "$GAME1_RESP" | jq -r '.game_id')
TOKEN_X1=$(echo "$GAME1_RESP" | jq -r '.player_token')
JOIN_SECRET1=$(echo "$GAME1_RESP" | jq -r '.join_secret')
echo "  Game 1 ID: $GAME1_ID"

echo "${YELLOW}[SETUP]${NC} Присоединяемся к Игре №1..."
JOIN1_RESP=$(curljson POST "$SERVER_URL/game/$GAME1_ID/join" "{\"join_secret\":\"$JOIN_SECRET1\"}")
TOKEN_O1=$(echo "$JOIN1_RESP" | jq -r '.player_token')
echo "  Player O joined"

echo "${YELLOW}[SETUP]${NC} Создаём Игру №2..."
GAME2_RESP=$(curl -s -X POST "$SERVER_URL/game/new")
GAME2_ID=$(echo "$GAME2_RESP" | jq -r '.game_id')
TOKEN_X2=$(echo "$GAME2_RESP" | jq -r '.player_token')
JOIN_SECRET2=$(echo "$GAME2_RESP" | jq -r '.join_secret')
echo "  Game 2 ID: $GAME2_ID"

echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 0: Smoke — создание и присоединение
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 0: Smoke — create + join + health ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' "$SERVER_URL/health")
check_status "Health check" "200" "$S" ""

# Create without join — should be WAITING
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/new")
check_status "Create game" "200" "$S" ""

# Join with wrong secret
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/join" \
  -H "Content-Type: application/json" -d '{"join_secret":"wrong-secret"}')
check_status "Join with wrong secret → 403" "403" "$S" "wrong secret"
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 1: X делает первый ход
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 1: Happy path — X moves first ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/move" \
  -H "Authorization: Bearer $TOKEN_X1" \
  -H "Content-Type: application/json" -d '{"player_id":"X","position":0}')
check_status "X moves 0" "200" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 2: Spoofing
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 2: Spoofing (токен X + тело O) ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/move" \
  -H "Authorization: Bearer $TOKEN_X1" \
  -H "Content-Type: application/json" -d '{"player_id":"O","position":4}')
check_status "Спуфинг → 403" "403" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 3: Out of Turn
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 3: Out of Turn (X дважды) ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/move" \
  -H "Authorization: Bearer $TOKEN_X1" \
  -H "Content-Type: application/json" -d '{"player_id":"X","position":2}')
check_status "X ходит дважды → 403" "403" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 4: O moves
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 4: Happy path — O responds ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/move" \
  -H "Authorization: Bearer $TOKEN_O1" \
  -H "Content-Type: application/json" -d '{"player_id":"O","position":4}')
check_status "O moves 4" "200" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 5: Cross-Game Attack
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 5: Cross-Game (токен Game1 → Game2) ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME2_ID/move" \
  -H "Authorization: Bearer $TOKEN_X1" \
  -H "Content-Type: application/json" -d '{"player_id":"X","position":0}')
check_status "Cross-Game POST → 403" "403" "$S" ""

# Cross-Game GET
S=$(curl -s -o /dev/null -w '%{http_code}' "$SERVER_URL/game/$GAME2_ID" \
  -H "Authorization: Bearer $TOKEN_X1")
check_status "Cross-Game GET → 403" "403" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 6: Без токена
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 6: Без Authorization ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/move" \
  -H "Content-Type: application/json" -d '{"player_id":"X","position":1}')
check_status "Без токена → 401" "401" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 7: Фальшивый JWT
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 7: Фальшивый JWT ━━━"
FAKE_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJYIiwiZ2FtZV9pZCI6ImZha2UiLCJleHAiOjk5OTk5OTk5OTl9.fake"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/move" \
  -H "Authorization: Bearer $FAKE_TOKEN" \
  -H "Content-Type: application/json" -d '{"player_id":"X","position":1}')
check_status "Фальшивый JWT → 401" "401" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 8: Позиция вне диапазона
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 8: Позиция 99 ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/move" \
  -H "Authorization: Bearer $TOKEN_X1" \
  -H "Content-Type: application/json" -d '{"player_id":"X","position":99}')
check_status "Позиция 99 → 422" "422" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 9: Занятая клетка
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 9: Занятая клетка ━━━"
# Создаём отдельную игру для чистоты теста
G9_RESP=$(curl -s -X POST "$SERVER_URL/game/new")
G9_ID=$(echo "$G9_RESP" | jq -r '.game_id')
G9_TX=$(echo "$G9_RESP" | jq -r '.player_token')
G9_SECRET=$(echo "$G9_RESP" | jq -r '.join_secret')
G9_JRESP=$(curl -s -X POST "$SERVER_URL/game/$G9_ID/join" \
  -H "Content-Type: application/json" -d "{\"join_secret\":\"$G9_SECRET\"}")
G9_TO=$(echo "$G9_JRESP" | jq -r '.player_token')
# X ходит на 0
curl -s -X POST "$SERVER_URL/game/$G9_ID/move" \
  -H "Authorization: Bearer $G9_TX" \
  -H "Content-Type: application/json" -d '{"player_id":"X","position":0}' > /dev/null
# Теперь ход O — пытается ходить на занятую клетку 0
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$G9_ID/move" \
  -H "Authorization: Bearer $G9_TO" \
  -H "Content-Type: application/json" -d '{"player_id":"O","position":0}')
check_status "Ход на занятую → 400" "400" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ТЕСТ 10: Присоединение с уже использованным secret
# ═══════════════════════════════════════════════════════════════════════════
echo "━━━ ТЕСТ 10: Replay join_secret ━━━"
S=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER_URL/game/$GAME1_ID/join" \
  -H "Content-Type: application/json" -d "{\"join_secret\":\"$JOIN_SECRET1\"}")
check_status "Replay join_secret → 403" "403" "$S" ""
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# ИТОГИ
# ═══════════════════════════════════════════════════════════════════════════
echo "=============================================="
echo "  РЕЗУЛЬТАТЫ RED TEAM ТЕСТИРОВАНИЯ"
echo "=============================================="
echo ""
echo "  Пройдено: ${GREEN}$PASS${NC}"
echo "  Провалено: ${RED}$FAIL${NC}"
echo "  Всего: $((PASS + FAIL))"
echo ""

if [ "$FAIL" -eq 0 ]; then
    echo "${GREEN}ВСЕ ТЕСТЫ ПРОЙДЕНЫ — Zero Trust подтверждён${NC}"
else
    echo "${RED}ОБНАРУЖЕНЫ УЯЗВИМОСТИ!${NC}"
    exit 1
fi