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
# Проверка версии средствами самого Python: код возврата, без вывода текста,
# чтобы не зависеть от экранирования внутри heredoc.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo
  echo "Нужен Python 3.10 или новее."
  echo "  macOS:  brew install python@3.12"
  echo "  затем:  /opt/homebrew/bin/python3.12 -m venv .venv && source .venv/bin/activate"
  echo "          pip install -r requirements.txt"
  exit 1
fi

echo "==> Виртуальное окружение"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Зависимости"
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q pytest

echo "==> Конфиги"
[ -f .env ] || { cp .env.example .env; echo "   создан .env — впишите ключи API"; }
[ -f config.yaml ] || { cp config.example.yaml config.yaml; echo "   создан config.yaml"; }

echo "==> Тесты"
python -m pytest tests -q

cat <<'MSG'

Готово. Дальше по порядку:

  1. Впишите ключи в .env  (testnet.bybit.com -> API -> создать ключ)
     Права ТОЛЬКО "Trade". Право на вывод средств НЕ включать.

  2. Активируйте окружение в новом окне терминала:
     source .venv/bin/activate

  3. Проверка таймфрейма:  python tools/timeframe_sweep.py --symbol ETHUSDT
  4. Бэктест:              python tools/backtest.py --symbol ETHUSDT --interval 60 --bars 5000
  5. Сухой прогон:         python -m bot.main --config config.yaml
     (в config.yaml стоит testnet: true и dry_run: true — ордера только в лог)

MSG
