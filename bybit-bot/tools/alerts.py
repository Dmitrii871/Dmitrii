#!/usr/bin/env python3
"""Сигналка: уведомления на экран Mac, когда RSI входит в крайние зоны.

Следит за часовым RSI(14) по списку монет. Когда RSI опускается к порогу
покупки (по умолчанию 30) или поднимается выше порога продажи (70) —
показывает macOS-уведомление со звуком. Повторно не спамит: следующее
уведомление по той же монете придёт только после выхода из зоны.

ВАЖНО: это информация, а не команда к действию. Правило «покупать RSI 30»
на 1.4 годах истории убыточно (tools/dip.py 12000) — сигналка нужна тем,
кто торгует руками и принимает риск на себя.

Запуск в фоне:   ./alerts.sh
Остановка:       ./alerts.sh stop
Пороги:          ./alerts.sh 25 75   (покупка/продажа)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.indicators import bollinger_pct_b, rsi  # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
INTERVAL = "15"            # 15-минутные свечи; третьим аргументом можно сменить
CHECK_EVERY = 120


def notify(title: str, text: str) -> None:
    """Уведомление на экран Mac; в терминал — всегда (если Mac без osascript)."""
    print(f"{time.strftime('%H:%M:%S')} | {title}: {text}", flush=True)
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "{title}" sound name "Glass"'],
            timeout=10, check=False,
        )
    except FileNotFoundError:
        pass                                   # не macOS — остаёмся в терминале


def main() -> int:
    buy_lvl = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    sell_lvl = float(sys.argv[2]) if len(sys.argv) > 2 else 70.0
    interval = sys.argv[3] if len(sys.argv) > 3 else INTERVAL

    from pybit.unified_trading import HTTP
    http = HTTP(testnet=False)

    zone: dict[str, str] = {s: "" for s in SYMBOLS}       # RSI: "", "low", "high"
    bb_zone: dict[str, str] = {s: "" for s in SYMBOLS}    # Боллинджер отдельно
    print(f"Сигналка запущена: RSI(14) на {interval}-минутках, покупка<={buy_lvl:g}, "
          f"продажа>={sell_lvl:g}, проверка раз в {CHECK_EVERY // 60} мин")
    notify("Сигналка запущена", f"RSI: зоны {buy_lvl:g} / {sell_lvl:g}")

    while True:
        for sym in SYMBOLS:
            try:
                kl = http.get_kline(category="linear", symbol=sym,
                                    interval=interval, limit=60)["result"]["list"]
                closes = [float(r[4]) for r in reversed(kl)][:-1]
                series = rsi(closes)
                bb = bollinger_pct_b(closes)
                if not series or not bb:
                    continue
                r = series[-1]
                b = bb[-1]
                price = closes[-1]
            except Exception as exc:  # noqa: BLE001 — сигналка живучая
                print(f"{sym}: {exc}", flush=True)
                continue

            if r <= buy_lvl and zone[sym] != "low":
                zone[sym] = "low"
                notify(f"{sym}: RSI {r:.1f}",
                       f"Зона покупки (<= {buy_lvl:g}). Цена {price:g}")
            elif r >= sell_lvl and zone[sym] != "high":
                zone[sym] = "high"
                notify(f"{sym}: RSI {r:.1f}",
                       f"Зона продажи (>= {sell_lvl:g}). Цена {price:g}")
            elif buy_lvl + 3 < r < sell_lvl - 3:
                zone[sym] = ""                 # вышли из зоны — можно сигналить снова

            if b <= 0.02 and bb_zone[sym] != "low":
                bb_zone[sym] = "low"
                notify(f"{sym}: НИЖНЯЯ полоса Боллинджера",
                       f"%B {b:.2f} — зона покупки по твоей системе. Цена {price:g}")
            elif b >= 0.98 and bb_zone[sym] != "high":
                bb_zone[sym] = "high"
                notify(f"{sym}: ВЕРХНЯЯ полоса Боллинджера",
                       f"%B {b:.2f} — зона продажи. Цена {price:g}")
            elif 0.15 < b < 0.85:
                bb_zone[sym] = ""
            time.sleep(0.3)
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    sys.exit(main())
