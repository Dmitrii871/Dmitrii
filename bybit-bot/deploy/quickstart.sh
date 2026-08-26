#!/usr/bin/env bash
# Установка одной командой:  bash deploy/quickstart.sh
set -euo pipefail

cd "$(dirname "$0")/.."
echo "==> Проверка Python"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 не найден."
  echo "  macOS:  brew install python@3.12   (или xcode-select --install)"
  echo "  Ubuntu: sudo apt install python3 python3-venv python3-pip"
  exit 1
fi
python3 --version
python3 - <<'PYCHECK' || exit 1
import sys
if sys.version_info < (3, 10):
    print(f"
Нужен Python 3.10 или новее, найден {sys.version.split()[0]}.")
    print("  macOS:  brew install python@3.12")
    print("  затем:  /opt/homebrew/bin/python3.12 -m venv .venv")
    sys.exit(1)
PYCHECK

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
     Права ТОЛЬКО "Trade". Право на вывод средств НЕ включать.
  2. Проверка таймфрейма:  python tools/timeframe_sweep.py --symbol ETHUSDT
  3. Бэктест:              python tools/backtest.py --symbol ETHUSDT --interval 60 --bars 5000
  4. Сухой прогон:         python -m bot.main --config config.yaml
     (в config.yaml стоит testnet: true и dry_run: true — ордера только в лог)

MSG
