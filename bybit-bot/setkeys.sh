#!/bin/sh
# Безопасный ввод API-ключей: ./setkeys.sh
# Вставленные значения НЕ отображаются на экране и не попадают в историю
# команд — скриншот терминала после этого безопасен.
cd "$(dirname "$0")" || exit 1
trap 'stty echo 2>/dev/null' EXIT INT

printf 'Вставьте API Key (ввод НЕВИДИМ — так задумано) и нажмите Enter: '
stty -echo 2>/dev/null; read -r KEY; stty echo 2>/dev/null; printf '\n'
printf 'Вставьте API Secret (ввод невидим) и нажмите Enter: '
stty -echo 2>/dev/null; read -r SECRET; stty echo 2>/dev/null; printf '\n'

if [ -z "$KEY" ] || [ -z "$SECRET" ]; then
    echo "Пустое значение — ничего не записано. Запустите ./setkeys.sh заново."
    exit 1
fi
umask 077
printf 'BYBIT_API_KEY=%s\nBYBIT_API_SECRET=%s\n' "$KEY" "$SECRET" > .env
echo "Сохранено: ключ ${#KEY} символов, секрет ${#SECRET} символов."
echo "Дальше: ./check.sh"
