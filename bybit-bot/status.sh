#!/bin/sh
# Полное состояние теста одной командой: ./status.sh
cd "$(dirname "$0")" && exec python3 tools/status.py "$@"
