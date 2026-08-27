#!/bin/sh
# Единственный правильный способ запуска: ./start.sh
# Останавливает прежний экземпляр, ЖДЁТ его настоящего выхода,
# запускает новый, не даёт Mac уснуть и показывает статус.
cd "$(dirname "$0")" || exit 1

if pgrep -f "python.*bot\.main" >/dev/null 2>&1; then
    echo "Останавливаю прежний экземпляр..."
    pkill -f "python.*bot\.main"
    i=0
    while pgrep -f "python.*bot\.main" >/dev/null 2>&1; do
        i=$((i+1))
        if [ "$i" -gt 40 ]; then
            echo "Прежний экземпляр не остановился за 40 с — прекращаю принудительно."
            pkill -9 -f "python.*bot\.main"
            sleep 2
            break
        fi
        sleep 1
    done
    echo "Остановлен."
fi

# журналы, испорченные прежними версиями (строки без заголовка), пересоздаём:
# дописывать в них бессмысленно — такой CSV не прочитает ни отчёт, ни Excel
for f in *_journal.csv; do
    [ -f "$f" ] || continue
    case "$(head -1 "$f")" in
        ts,*) ;;
        *) echo "Пересоздаю повреждённый $f"; rm -f "$f" ;;
    esac
done

: > bot.out
nohup python3 -m bot.main --config config.yaml >> bot.out 2>&1 &
PID=$!
echo "Бот запущен, PID $PID"

# не даём Mac уснуть, пока бот жив (на Linux caffeinate нет — пропускаем)
if command -v caffeinate >/dev/null 2>&1; then
    nohup caffeinate -i -w "$PID" >/dev/null 2>&1 &
    echo "caffeinate привязан — Mac не уснёт, пока бот работает"
fi

echo "Жду первый цикл (до 60 с)..."
i=0
while [ "$i" -lt 60 ]; do
    if ! kill -0 "$PID" 2>/dev/null; then
        break   # процесс умер — статус покажет хвост bot.out с причиной
    fi
    # появилась хоть одна строка данных в любом журнале — цикл прошёл
    if [ -n "$(find . -maxdepth 1 -name '*_journal.csv' -size +200c 2>/dev/null | head -1)" ]; then
        break
    fi
    i=$((i+2))
    sleep 2
done

exec python3 tools/status.py
