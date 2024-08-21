"""IRC bot that sets itself away when it doesn't say anything.

Connect this to your Irssi with irssiproxy or whatever you use.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from math import log2

import asyncirc
from timer import Timer

debug = asyncirc.debug


open


INF = float('inf')

IDLE_TIMEOUT = 5 * 60.0  # seconds

# The rule is that if we reset the idle timer 10% before or after its expiry,
# we multiply the timeout by a backoff factor.
IDLE_BACKOFF_FACTOR: float = 2
IDLE_MAX_TIMEOUT: float = 3600.0
IDLE_BACKOFF_MAX_EXP = int(log2(IDLE_MAX_TIMEOUT / IDLE_TIMEOUT))
IDLE_BACKOFF_DEADZONE = 1.0
IDLE_MAX_TIMEOUT = IDLE_TIMEOUT * IDLE_BACKOFF_FACTOR**IDLE_BACKOFF_MAX_EXP
IDLE_BACKOFF_DECAY = 24 * 3600.0


def selftest() -> None:
    assert Timer.never().is_never
    assert not Timer(0.0).is_never
    assert Timer(0).is_expired
    timer = Timer.never()
    intr = asyncirc.Interrupt()
    async def waiter() -> None:
        assert await intr.wait(timeout=timer.remaining)
    async def triggerer() -> None:
        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.0)
        intr.trigger()
        await wait_task
    asyncio.run(triggerer())


selftest()


async def idle_away(control: Control, *,
                    idle_timeout: float = IDLE_TIMEOUT,
                    backoff_factor: float = IDLE_BACKOFF_FACTOR,
                    backoff_max_exp: int = IDLE_BACKOFF_MAX_EXP,
                    backoff_deadzone: float = IDLE_BACKOFF_DEADZONE,
                    backoff_decay: float = IDLE_BACKOFF_DECAY) -> None:

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
