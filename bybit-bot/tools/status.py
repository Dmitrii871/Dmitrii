#!/usr/bin/env python3
"""Одна команда вместо десяти: полное состояние теста.

Отвечает на все вопросы, которые иначе приходится задавать по очереди:
жив ли бот, все ли символы отдают данные, что он видит прямо сейчас,
сколько сделок и — главное — какая доля выходов прошла мейкером.
"""
from __future__ import annotations

import csv
import re
import glob
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _proc() -> str:
    """Ищем процесс бота по списку процессов, а не через pgrep -f.

    pgrep -f сопоставляет со всей командной строкой и находит саму
    команду, в которой упомянут 'bot.main' — то есть отвечает 'запущен'
    даже когда бот мёртв. Здесь исключаем собственную ветку процессов.
    """
    mine = {os.getpid(), os.getppid()}
    try:
        out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                             text=True, timeout=5).stdout.splitlines()
    except Exception:  # noqa: BLE001
        return "не удалось проверить процесс"
    found = []
    for line in out:
        pid, _, args = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) in mine:
            continue
        low = args.lower()
        if "bot.main" in low and "python" in low and "status.py" not in low:
            found.append(pid)
    if not found:
        return "НЕ ЗАПУЩЕН"
    if len(found) > 1:
        return f"ВНИМАНИЕ: экземпляров {len(found)} (PID {', '.join(found)}) — должен быть один"
    expected = ""
    try:
        expected = Path("bot.pid").read_text().strip()
    except OSError:
        pass
    if expected and found[0] != expected:
        return (f"ВНИМАНИЕ: работает PID {found[0]}, но start.sh запускал {expected}. "
                "Это уцелевший СТАРЫЙ экземпляр — выполните ./start.sh")
    return f"работает, PID {found[0]}"


def _age(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return "?"
    delta = (datetime.now(timezone.utc) - dt).total_seconds()
    if delta < 90:
        return f"{delta:.0f} с назад"
    return f"{delta / 60:.0f} мин назад"


def _read(path: str) -> list[dict]:
    """Строки CSV; повреждённые или безголовые файлы — пустой список.

    Отчёт обязан пережить любой мусор в файлах: его задача — рассказать
    о проблеме, а не упасть от неё.
    """
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        return [r for r in rows if r.get("ts")]
    except Exception:  # noqa: BLE001
        return []


def _read_trades(path: str) -> list[dict]:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:  # noqa: BLE001
        return []


def _trades_from_log() -> dict:
    """Сделки текущей сессии по строкам [БУМАГА] в bot.out.

    Основной источник — файлы *_trades.csv, но у конфигов, созданных до
    их появления, файлы не велись: сделки совершались и existed только
    в памяти бота. Лог — единственный след, по нему хотя бы видно счёт.
    """
    out = {"entries": 0, "exits": 0, "maker": 0, "net": 0.0}
    lines: list[str] = []
    for name in ("bot.history.log", "bot.out"):
        try:
            lines += Path(name).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
    for line in lines:
        if "[БУМАГА] вход" in line:
            out["entries"] += 1
        elif "[БУМАГА] выход" in line:
            out["exits"] += 1
            if "(лимитный выход)" in line:
                out["maker"] += 1
            m = re.search(r"сделка ([+-]?[\d.]+) USDT", line)
            if m:
                out["net"] += float(m.group(1))
    return out


def main() -> int:
    os.chdir(Path(__file__).resolve().parents[1])
    print("=" * 66)
    print(f"  СОСТОЯНИЕ ТЕСТА  |  {datetime.now().strftime('%d.%m %H:%M')}")
    print("=" * 66)
    proc = _proc()
    print(f"Процесс бота: {proc}")
    if "работает" not in proc and Path("bot.out").exists():
        print("\nПоследние строки bot.out — почему он не работает:")
        lines = Path("bot.out").read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-12:]:
            print(f"  {line}")

    # ---------------------------------------------------------- символы
    journals = sorted(glob.glob("*_journal.csv"))
    if not journals:
        print("\nЖурналов нет — бот ещё не сделал ни одного цикла.")
        return 0

    print(f"\nСИМВОЛЫ ({len(journals)})")
    print(f"  {'символ':<12}{'строк':>7}{'обновлён':>14}  что видит сейчас")
    stale = []
    for path in journals:
        sym = path.split("_")[0]
        rows = _read(path)
        if not rows:
            print(f"  {sym:<12}{0:>7}{'—':>14}  пусто или повреждён — ./start.sh пересоздаст")
            stale.append(sym)
            continue
        last = rows[-1]
        age = _age(last["ts"])
        if "мин" in age and int(age.split()[0]) > 5:
            stale.append(sym)
        vl, vs = last.get("голоса_лонг", ""), last.get("голоса_шорт", "")
        rsi = last.get("rsi", "")
        if rsi:
            seen = f"RSI {rsi:<6} голоса {vl or '?'}/{vs or '?'}"
        elif last.get("канал"):
            seen = f"канал {last['канал']}"
        else:
            seen = f"RSI {'—':<6} голоса {vl or '?'}/{vs or '?'}"
        if last.get("режим"):
            seen += f"  {last['режим']}"
        if last.get("позиция"):
            seen += f"  << ПОЗИЦИЯ {last['позиция']}"
        print(f"  {sym:<12}{len(rows):>7}{age:>14}  {seen}")
        decisions = [r for r in rows if r.get("действие") not in ("", "нет")]
        if decisions:
            d = decisions[-1]
            print(f"  {'':12}последнее решение: {d.get('действие','')} "
                  f"({_age(d.get('ts',''))}) — {d.get('причина','')[:80]}")
    if stale:
        print(f"  ОТСТАЮТ (>5 мин): {', '.join(stale)}")

    # ---------------------------------------------------------- сделки
    trade_files = sorted(glob.glob("*_trades.csv"))
    trades: list[dict] = []
    per_symbol: Counter = Counter()
    for path in trade_files:
        rows = [r for r in _read_trades(path) if r.get("net")]
        per_symbol[path.split("_")[0]] = len(rows)
        trades.extend(rows)

    open_pos = []
    for path in journals:
        rows = _read(path)
        if rows and rows[-1].get("позиция"):
            open_pos.append(f"{path.split('_')[0]} {rows[-1]['позиция']}")
    print(f"\nОТКРЫТЫЕ ПОЗИЦИИ: {', '.join(open_pos) if open_pos else 'нет'}")
    print(f"СДЕЛКИ (закрытые): {len(trades)}")
    log_t = _trades_from_log()
    if log_t["exits"] > len(trades):
        print(f"  по логу текущей сессии: входов {log_t['entries']}, "
          f"выходов {log_t['exits']} (мейкером {log_t['maker']}), "
          f"итог {log_t['net']:+.4f} USDT")
        print("  (файлы сделок не велись — конфиг старее этой возможности; "
              "после ./start.sh будут вестись)")
    if not trades:
        if not log_t["exits"]:
            print("  Пока ни одной. Вход бывает лишь на закрытии свечи —")
            print("  при часовом таймфрейме это до нескольких сделок в сутки.")
        return 0

    nets = [float(t["net"]) for t in trades]
    wins = [n for n in nets if n > 0]
    maker = [t for t in trades if str(t.get("maker_exit", "")).lower() in ("true", "1")]
    gross_loss = abs(sum(n for n in nets if n <= 0))
    pf = (sum(wins) / gross_loss) if gross_loss else None

    print(f"  винрейт          {len(wins) / len(trades) * 100:.0f}%  ({len(wins)} из {len(trades)})")
    print(f"  ВЫХОДЫ МЕЙКЕРОМ  {len(maker) / len(trades) * 100:.0f}%  ({len(maker)} из {len(trades)})")
    print(f"  профит-фактор    {f'{pf:.2f}' if pf else '—'}")
    print(f"  комиссии         {sum(float(t['fees']) for t in trades):.4f} USDT")
    print(f"  ИТОГ             {sum(nets):+.4f} USDT")
    print("  по символам:     " + ", ".join(f"{k} {v}" for k, v in per_symbol.most_common() if v))
    print("  причины выхода:  " + ", ".join(
        f"{k} {v}" for k, v in Counter(t["reason"] for t in trades).most_common()))

    print("\nВЕРДИКТ")
    if len(trades) < 30:
        print(f"  Рано считать: {len(trades)} сделок из 30 нужных.")
    elif len(maker) / len(trades) >= 0.70 and sum(nets) > 0:
        print("  Мейкерских выходов ≥70% и итог положительный — стратегия рабочая.")
    elif len(maker) / len(trades) < 0.70:
        print(f"  Мейкерских выходов {len(maker) / len(trades) * 100:.0f}% при нужных 70%.")
        print("  Комиссия съедает край — на реальные деньги выходить нельзя.")
    else:
        print("  Мейкер-доля в норме, но итог отрицательный — дело в сигнале, не в исполнении.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
