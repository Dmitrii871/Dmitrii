"""Защита от второго запущенного экземпляра.

Два процесса, пишущих в один журнал, дают перемешанный лог с временными
метками вразнобой — по нему невозможно понять, что происходило. Хуже
того, оба открывают позиции по одному счёту, и лимит риска считается
дважды по половине картины.

Блокировка через flock, а не PID-файл: ядро снимает её само при любом
завершении процесса, включая kill -9 и падение. PID-файл после такого
остаётся и блокирует нормальный запуск.
"""
from __future__ import annotations

import errno
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Другой экземпляр бота уже работает с этим каталогом."""


@contextmanager
def single_instance(path: str = "./bot.lock"):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            other = ""
            try:
                other = os.pread(fd, 32, 0).decode().strip()
            except OSError:
                pass
            raise AlreadyRunning(
                f"Бот уже запущен (PID {other or 'неизвестен'}), файл блокировки {p}.\n"
                "Остановите прежний экземпляр и дождитесь его выхода:\n"
                "  pkill -f bot.main && sleep 3 && pgrep -fl bot.main\n"
                "Вторая строка должна ничего не вывести."
            ) from exc
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        yield p
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
