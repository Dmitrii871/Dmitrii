#!/usr/bin/env bash
# Установка одной командой:  bash deploy/quickstart.sh
set -euo pipefail

cd "$(dirname "$0")/.."
echo "==> Проверка Python"
python3 --version

echo "==> Виртуальное окружение"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Зависимости"
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "==> Конфиги"
[ -f .env ] || { cp .env.example .env; echo "   создан .env — впишите ключи API"; }
[ -f config.yaml ] || { cp config.example.yaml config.yaml; echo "   создан config.yaml"; }

echo "==> Тесты"
pip install -q pytest && python -m pytest tests -q

cat <<'MSG'

Готово. Дальше по порядку:

  1. Впишите ключи в .env  (testnet.bybit.com -> API -> создать ключ)
  2. Проверка таймфрейма:  python tools/timeframe_sweep.py --symbol ETHUSDT
  3. Бэктест:              python tools/backtest.py --symbol ETHUSDT --interval 60 --bars 5000
  4. Сухой прогон:         python -m bot.main --config config.yaml
     (в config.yaml стоит testnet: true и dry_run: true — ордера только в лог)

MSG
