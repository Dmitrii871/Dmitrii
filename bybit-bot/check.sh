#!/bin/sh
# Проверка подключения к бирже без единого ордера: ./check.sh
cd "$(dirname "$0")" || exit 1
PY=python3
for cand in ./.venv/bin/python3 ../.venv/bin/python3 "$HOME/Dmitrii/.venv/bin/python3"; do
    [ -x "$cand" ] && PY="$cand" && break
done
exec "$PY" -m bot.main --config config.yaml --check
