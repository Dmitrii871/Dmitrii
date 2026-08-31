#!/bin/sh
# Автозапуск боевого бота через launchd (штатный механизм macOS).
#   ./autostart.sh      — включить: бот стартует при входе в систему,
#                         перезапускается после падений, Mac не спит
#   ./autostart.sh off  — выключить автозапуск и остановить бота
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
PLIST="$HOME/Library/LaunchAgents/com.bybitbot.trend.plist"

if [ "$1" = "off" ]; then
    launchctl unload "$PLIST" 2>/dev/null
    rm -f "$PLIST"
    pkill -f "bot.main" 2>/dev/null
    echo "Автозапуск выключен, бот остановлен."
    exit 0
fi

PY="$DIR/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
if ! "$PY" -c "import yaml, pybit, dotenv" 2>/dev/null; then
    echo "Питон '$PY' не видит библиотеки бота — автозапуск не настроен."
    exit 1
fi
if ! grep -q "dry_run: false" config.yaml; then
    echo "ВНИМАНИЕ: в config.yaml бумажный режим — автозапуск поднимет бота в нём."
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.bybitbot.trend</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>$PY</string>
        <string>-m</string>
        <string>bot.main</string>
        <string>--config</string>
        <string>$DIR/config.yaml</string>
        <string>--live</string>
        <string>--yes</string>
    </array>
    <key>WorkingDirectory</key><string>$DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$DIR/bot.out</string>
    <key>StandardErrorPath</key><string>$DIR/bot.out</string>
</dict>
</plist>
EOF

# прежний ручной экземпляр освобождает блокировку — его место займёт launchd
pkill -f "bot.main" 2>/dev/null
sleep 3
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST"
echo "Автозапуск включён: бот стартует при входе в систему и"
echo "перезапускается после падений. Подтверждение YES больше не нужно."
echo "Проверка через полминуты: ./status.sh | Выключить: ./autostart.sh off"
