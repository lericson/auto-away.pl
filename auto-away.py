"""IRC bot that sets itself away when it doesn't say anything.

Connect this to your Irssi with irssiproxy or whatever you use.
"""

from __future__ import annotations

import sys
import time
import asyncio
from math import log2
from typing import Callable
from dataclasses import dataclass, field

import asyncirc


debug = asyncirc.debug


inf = float('inf')

idle_timeout = 5 * 60.0  # seconds

# The rule is that if we reset the idle timer 10% before or after its expiry,
# we multiply the timeout by a backoff factor.
idle_backoff_factor: float = 2
idle_max_timeout: float = 3600.0
idle_backoff_max_exp = int(log2(idle_max_timeout / idle_timeout))
idle_backoff_deadzone = 1.0
idle_max_timeout = idle_timeout * idle_backoff_factor**idle_backoff_max_exp
idle_backoff_decay = 24 * 3600.0


class Timer:

    __slots__ = 'started_at', 'duration'

    started_at: float
    duration:   float

    clock: Callable[[], float] = time.monotonic

    def __init__(self,
                 duration: float,
                 *,
                 elapsed: float | None = None,
                 starts_in: float | None = None,
                 started_at: float | None = None) -> None:

        if (elapsed is None) + (starts_in is None) + (started_at is None) < 2:
            raise ValueError('specify one of elapsed, starts_in, started_at')

        if started_at is None:
            started_at = self.clock()

        if elapsed is not None:
            started_at -= elapsed

        if starts_in is not None:
            started_at += starts_in

        self.started_at = started_at

        if hasattr(duration, 'duration'):
            duration = duration.duration

        self.duration = duration

    def __repr__(self) -> str:
        tp = type(self)
        name = f'{tp.__module__}.{tp.__name__}'
        if self.is_started:
            return f'{name}({self.remaining:.5g}, elapsed={self.elapsed:.5g})'
        else:
            return f'{name}({self.remaining:.5g}, starts_in={self.starts_in:.5g})'

    @property
    def is_running(self) -> bool:
        return self.started_at < self.clock() < self.expires

    @property
    def is_started(self) -> bool:
        return self.started_at < self.clock()

    @property
    def is_expired(self) -> bool:
        return self.expires < self.clock()

    @property
    def is_never(self) -> bool:
        return float('inf') <= self.expires

    @property
    def expires(self) -> float:
        return self.started_at + self.duration

    @property
    def elapsed(self) -> float:
        return self.clock() - self.started_at

    @property
    def remaining(self) -> float:
        return self.duration - self.elapsed

    @property
    def starts_in(self) -> float:
        return -self.elapsed

    @classmethod
    def never(cls) -> Timer:
        return cls(float('inf'))


assert Timer.never().is_never
assert not Timer(0.0).is_never
assert Timer(0).is_expired


async def idle_away(control: Control, *,
                    idle_timeout: float = idle_timeout,
                    backoff_factor: float = idle_backoff_factor,
                    backoff_max_exp: int = idle_backoff_max_exp,
                    backoff_deadzone: float = idle_backoff_deadzone,
                    backoff_decay: float = idle_backoff_decay) -> None:

    def make_idle_timer() -> Timer:
        return Timer(idle_timeout * (backoff_factor ** backoff_exp))

    def make_backoff_timer() -> Timer:
        duration = idle_timer.duration * backoff_deadzone
        start_at = idle_timer.expires - duration / 2
        return Timer(duration, started_at=start_at)

    backoff_exp:     int = 0
    idle_timer:    Timer = make_idle_timer()
    backoff_timer: Timer = make_backoff_timer()
    decay_timer:   Timer = Timer(backoff_decay)

    while True:

        timeout = idle_timer.remaining if not idle_timer.is_never else None

        debug()
        debug(f'   {idle_timer = }')

        interrupted = await control.idle_interrupt.wait(timeout=timeout)

        debug(f'  {interrupted = }')
        debug(f'   {idle_timer = }')
        debug(f'{backoff_timer = }')
        debug(f'  {decay_timer = }')

        if interrupted:

            if backoff_timer.is_running:
                backoff_exp += 1
                backoff_exp  = min(backoff_exp, backoff_max_exp)

            if decay_timer.is_expired:
                backoff_exp -= 1
                backoff_exp  = max(backoff_exp, 0)
                decay_timer  = Timer(backoff_decay)

            debug(f'  {backoff_exp = }')

            idle_timer    = make_idle_timer()
            backoff_timer = make_backoff_timer()

            await control.set_away(False)

        elif idle_timer.is_expired:

            idle_timer = Timer.never()

            await control.set_away(True)

        else:
            raise RuntimeError('logically impossible')


@dataclass
class Control:

    clients: dict[str, asyncirc.Client] = field(default_factory=dict)

    idle_interrupt: asyncirc.Interrupt = field(default_factory=asyncirc.Interrupt, repr=False)

    async def set_away(self, awayness: bool) -> None:
        async with asyncio.TaskGroup() as tg:
            for client in self.clients.values():
                tg.create_task(client.set_away(awayness))

    async def unix_socket_client(self, path: str) -> None:
        reader, writer = await asyncio.open_unix_connection(path)
        client = asyncirc.Client(reader, writer, idle_interrupt=self.idle_interrupt)
        self.clients[path] = client
        try:
            await client.communicate()
        finally:
            del self.clients[path]
            writer.close()
            await writer.wait_closed()


async def main(args: list[str] = sys.argv[1:]) -> None:
    control = Control()
    idle_task = asyncio.create_task(idle_away(control))
    async with asyncio.TaskGroup() as tg:
        for path in args:
            tg.create_task(control.unix_socket_client(path))
    idle_task.cancel()


if __name__ == '__main__':
    asyncio.run(main())
