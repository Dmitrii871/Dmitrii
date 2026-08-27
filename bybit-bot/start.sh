#!/bin/sh
# Единственный правильный способ запуска: ./start.sh
# Останавливает прежний экземпляр, ЖДЁТ его настоящего выхода,
# запускает новый, не даёт Mac уснуть и показывает статус.
cd "$(dirname "$0")" || exit 1

# Ищем питон с библиотеками бота сами: в новом окне терминала venv не
# активирован, и системный python3 падает с "No module named 'yaml'".
PY=python3
for cand in ./.venv/bin/python3 ../.venv/bin/python3 "$HOME/Dmitrii/.venv/bin/python3"; do
    if [ -x "$cand" ]; then
        PY="$cand"
        break
    fi
done
if ! "$PY" -c "import yaml, pybit, dotenv" 2>/dev/null; then
    echo "Питон '$PY' не видит библиотеки бота (yaml/pybit/dotenv)."
    echo "Установите их один раз:"
    echo "  $PY -m pip install -r requirements.txt"
    exit 1
fi
echo "Питон: $PY"

if pgrep -f "[Pp]ython.*bot[.]main" >/dev/null 2>&1; then
    echo "Останавливаю прежний экземпляр..."
    pkill -f "[Pp]ython.*bot[.]main"
    i=0
    while pgrep -f "[Pp]ython.*bot[.]main" >/dev/null 2>&1; do
        i=$((i+1))
        if [ "$i" -gt 40 ]; then
            echo "Прежний экземпляр не остановился за 40 с — прекращаю принудительно."
            pkill -9 -f "[Pp]ython.*bot[.]main"
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

# лог прежней сессии не выбрасываем: в нём след сделок, статус его читает
if [ -s bot.out ]; then
    cat bot.out >> bot.history.log
fi
: > bot.out
nohup "$PY" -m bot.main --config config.yaml >> bot.out 2>&1 &
PID=$!
echo "$PID" > bot.pid
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
    # цикл прошёл, когда журнал обновил ИМЕННО новый процесс: bot.pid
    # создан при этом запуске, значит файлы новее него — свежие.
    # Сравнение с размером ловило строки прежнего экземпляра.
    if [ -n "$(find . -maxdepth 1 -name '*_journal.csv' -newer bot.pid 2>/dev/null | head -1)" ]; then
        break
    fi
    i=$((i+2))
    sleep 2
done

exec "$PY" tools/status.py
