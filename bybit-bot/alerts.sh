#!/bin/sh
# Сигналка RSI: ./alerts.sh [порог_покупки] [порог_продажи] | ./alerts.sh stop
cd "$(dirname "$0")" || exit 1
if [ "$1" = "stop" ]; then
    pkill -f "tools/alerts.py" && echo "Сигналка остановлена" || echo "Сигналка не была запущена"
    exit 0
fi
PY=python3
for cand in ./.venv/bin/python3 ../.venv/bin/python3; do
    [ -x "$cand" ] && PY="$cand" && break
done
pkill -f "tools/alerts.py" 2>/dev/null
nohup "$PY" tools/alerts.py "$@" >> alerts.out 2>&1 &
echo "Сигналка запущена в фоне (журнал: alerts.out). Остановка: ./alerts.sh stop"
