#!/bin/bash
# =============================================================================
# Zero Trust Game Bus — полный цикл: сборка → запуск → тестирование
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=============================================="
echo "  ZERO TRUST GAME BUS"
echo "  Сборка и развёртывание"
echo "=============================================="

# ─── Очистка предыдущих запусков ───
echo ""
echo "[1/4] Останавливаем предыдущие контейнеры..."
docker compose down -v 2>/dev/null || true

# ─── Сборка ───
echo ""
echo "[2/4] Сборка сервера..."
docker compose build server

# ─── Запуск ───
echo ""
echo "[3/4] Запуск контейнеров..."
docker compose up -d

echo ""
echo "  Ожидание готовности сервера..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "  Сервер готов! (попытка $i)"
        break
    fi
    sleep 1
done

# ─── Тестирование ───
echo ""
echo "[4/4] Запуск тестов..."
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  АТАКУЮЩИЙ: RED TEAM TESTS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
docker compose exec attacker sh /tests/run_attacks.sh || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  СЕТЕВАЯ ИЗОЛЯЦИЯ (из player_x)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
docker compose exec player_x sh /tests/network_isolation.sh || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  СЕТЕВАЯ ИЗОЛЯЦИЯ Docker (inspect)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "player_x_net (контейнеры):"
docker network inspect databustest_player_x_net -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null || echo "  (не найдена)"
echo ""
echo "player_o_net (контейнеры):"
docker network inspect databustest_player_o_net -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null || echo "  (не найдена)"
echo ""
echo "admin_net (контейнеры):"
docker network inspect databustest_admin_net -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null || echo "  (не найдена)"

echo ""
echo "=============================================="
echo "  Тестирование завершено"
echo "=============================================="
echo ""
echo "  Для ручных тестов:"
echo "    docker compose exec attacker sh"
echo "    docker compose exec player_x sh"
echo "    docker compose exec player_o sh"
echo ""
echo "  Для остановки:"
echo "    docker compose down -v"