"""IRC bot that sets itself away when it doesn't say anything.

Connect this to your Irssi with irssiproxy or whatever you use.
"""

import re
import sys
import time
import asyncio
from math import log2
from dataclasses import dataclass, field


inf = float('inf')

pat = r'''
    \s*
    # Time specification is apparently a thing
    (?:
        @time=
        (?P<ts>\d\d\d\d-\d\d-\d\dT\d\d:\d\d:\d\d.\d\d\dZ)
        \s+
    )?
    (?:
        :
        (?P<addr>
            (?:(?P<nick>[^!@\s]+)!)?
            (?:(?P<user>[^@\s]+)@)?
            (?P<host>[^\s]+)
        )
        \s+
    )?
    \s*
    (?P<command>\w+)
    \s*
    (?:
        (?P<spargs>.*?)
        \s*
        (?:
            :
            (?P<text>[^\r\n]*)
        )?
    )
    (?:\r\n|[\r\n])
'''

pat = re.compile(pat, re.VERBOSE)

idle_timeout = 5 * 60.0  # seconds

# The rule is that if we reset the idle timer 10% before or after its expiry,
# we multiply the timeout by a backoff factor.
idle_backoff_factor = 2
idle_max_timeout = 3600.0
idle_backoff_max_exp = int(log2(idle_max_timeout / idle_timeout))
idle_backoff_deadzone = 1.0
idle_max_timeout = idle_timeout*idle_backoff_factor**idle_backoff_max_exp
idle_backoff_decay = 24 * 3600.0


@(lambda f: f() or f)
def selftest():
    ts = '2023-10-02T06:27:18.020Z'
    tests = [(f'@time={ts} :nick!user@host COMMAND arg :long text\r\n', (ts,   'nick!user@host', 'nick', 'user', 'host', 'COMMAND', 'arg', 'long text')),
             (f'@time={ts} :nick!user@host COMMAND     :long text\r\n', (ts,   'nick!user@host', 'nick', 'user', 'host', 'COMMAND', '', 'long text')),
             (f'@time={ts} :nick!user@host COMMAND arg           \r\n', (ts,   'nick!user@host', 'nick', 'user', 'host', 'COMMAND', 'arg', None)),
             (f'@time={ts} :nick!user@host COMMAND arg           \r\n', (ts,   'nick!user@host', 'nick', 'user', 'host', 'COMMAND', 'arg', None)),
             ( '           :nick!user@host COMMAND arg           \r\n', (None, 'nick!user@host', 'nick', 'user', 'host', 'COMMAND', 'arg', None)),
             ( '                     :host COMMAND arg           \r\n', (None,           'host',   None,   None, 'host', 'COMMAND', 'arg', None)),
             (':libera.proxy 333 lericson ##python-offtopic mawk!mawk@wireguard/contributor/mawk 1690531926\r\n', (None, 'libera.proxy', None, None, 'libera.proxy', '333', 'lericson ##python-offtopic mawk!mawk@wireguard/contributor/mawk 1690531926', None))]
    for line, reference in tests:
        match = re.match(pat, line)
        try:
            assert match is None or reference is not None
            assert reference is None or match.groups() == reference
        except AssertionError:
            print(f'{line = }')
            print(f'{reference = }')
            print(f'{match = }')
            if match:
                print(f'{match.groups() = }')
            raise


class Interrupt(asyncio.mixins._LoopBoundMixin):

    def __init__(self):
        super().__init__()
        self._waiters = []

    def __repr__(self):
        res = super().__repr__()
        return f'<{res[1:-1]} [waiters:{len(self._waiters)}]>'

    def trigger(self):
        for fut in self._waiters:
            if not fut.done():
                fut.set_result(True)

    async def wait(self, *, timeout=inf):
        fut = self._get_loop().create_future()
        self._waiters.append(fut)
        aw = asyncio.wait_for(fut, timeout=timeout)
        try:
            await aw
            return True
        except TimeoutError:
            return False
        finally:
            self._waiters.remove(fut)


class Timer():
    __slots__ = 'started_at', 'duration'
    clock = time.time

    def __init__(self, duration, *, started_at=None):
        self.started_at = self.clock() if started_at is None else started_at
        if hasattr(duration, 'duration'):
            duration = duration.duration
        self.duration = duration

    @property
    def is_running(self):
        return self.started_at < self.clock() < self.expires

    @property
    def is_started(self):
        return self.started_at < self.clock()

    @property
    def is_expired(self):
        return self.expires < self.clock()

    @property
    def expires(self):
        return self.started_at + self.duration

    @property
    def elapsed(self):
        return self.clock() - self.started_at

    @property
    def remaining(self):
        return self.duration - self.elapsed

    @classmethod
    def never(cls):
        return cls(float('inf'))

    async def __await__(self):
        if not self.is_expired:
            await asyncio.sleep(self.remaining)
        else:
            raise ValueError('timer is expired')


async def idle_away(control, *,
                    idle_timeout=idle_timeout,
                    backoff_factor=idle_backoff_factor,
                    backoff_max_exp=idle_backoff_max_exp,
                    backoff_deadzone=idle_backoff_deadzone,
                    backoff_decay=idle_backoff_decay):

    def make_idle_timer():
        return Timer(idle_timeout * (backoff_factor ** backoff_exp))

    def make_backoff_timer():
        duration = idle_timer.duration * backoff_deadzone
        start_at = idle_timer.expires - duration / 2
        return Timer(duration, started_at=start_at)

    backoff_exp   = 0
    idle_timer    = make_idle_timer()
    backoff_timer = make_backoff_timer()
    decay_timer   = Timer(backoff_decay)

    while True:

        timeout = idle_timer.remaining if idle_timer else None

        if await control.idle_interrupt.wait(timeout=timeout):

            # Idle timer was interrupted.

            if backoff_timer.is_running:
                backoff_exp += 1
                backoff_exp = min(backoff_exp, backoff_max_exp)
                print(f'{backoff_exp = }')
            else:
                print(f'{backoff_timer.elapsed = }, {backoff_timer.remaining = }')

            if decay_timer.is_expired:
                backoff_exp -= 1
                backoff_exp = max(backoff_exp, 0)
                decay_timer = Timer(backoff_decay)
                print(f'{backoff_exp = }')

            idle_timer    = make_idle_timer()
            backoff_timer = make_backoff_timer()

            await control.set_away(False)

        elif idle_timer.is_expired:

            # Idle timer expired.

            idle_timer = None

            await control.set_away(True)


@dataclass
class Client:
    reader: asyncio.StreamReader = field(repr=False)
    writer: asyncio.StreamWriter = field(repr=False)

    my_nick: str = None
    is_away: bool = None

    idle_interrupt: Interrupt = field(default_factory=Interrupt, repr=False)
    away_changed: Interrupt = field(default_factory=Interrupt, repr=False)

    async def set_away(self, awayness):
        awaycmd = b'AWAY :Auto-away\r\n' if awayness else b'AWAY\r\n'
        if self.is_away != awayness:
            self.writer.write(awaycmd)
            await self.away_changed.wait()
            assert self.is_away == awayness

    async def communicate(self):

        self.my_nick = 'e'
        self.is_away = False

        self.writer.write(b'USER a b c d\r\n')
        self.writer.write(f'NICK {self.my_nick}\r\n'.encode('utf-8'))

        await self.writer.drain()

        async for line in self.reader:

            try:
                line = line.decode('utf-8')
            except UnicodeDecodeError as e:
                print(f'line is not utf-8: {line!r}', file=sys.stderr)
                print('falling back to latin1', file=sys.stderr)
                print(e, file=sys.stderr)
                line = line.decode('latin1')

            match = re.match(pat, line)
            if match is None:
                print(f'pat match failed, {line = }', file=sys.stderr)
                continue

            (ts, addr, nick, user, host, cmd, spargs, text) = match.groups()

            cmd = cmd.lower()

            args = spargs.split() if spargs else []
            if text is not None:
                args.append(text)

            match (cmd, *args):

                case 'nick', new_nick if nick == self.my_nick:
                    self.my_nick = new_nick
                    print(f'{self.my_nick = }')

                case 'privmsg', _, _ if nick == self.my_nick:
                    self.idle_interrupt.trigger()

                case '305', who, _ if who == self.my_nick:
                    self.is_away = False
                    print(f'{self.is_away = }')
                    self.away_changed.trigger()

                case '306', who, _ if who == self.my_nick:
                    self.is_away = True
                    print(f'{self.is_away = }')
                    self.away_changed.trigger()

            await self.writer.drain()


@dataclass
class Control:

    clients: dict[asyncio.Task, Client] = field(default_factory=dict)

    idle_interrupt: Interrupt = field(default_factory=Interrupt, repr=False)

    async def set_away(self, awayness):
        async with asyncio.TaskGroup() as tg:
            for client in self.clients.values():
                tg.create_task(client.set_away(awayness))

    async def unix_socket_client(self, path):
        reader, writer = await asyncio.open_unix_connection(path)
        client = Client(reader, writer, idle_interrupt=self.idle_interrupt)
        task = client.communicate()
        self.clients[task] = client
        try:
            await task
        finally:
            del self.clients[task]
            writer.close()
            await writer.wait_closed()


async def main(args=sys.argv[1:]):
    control = Control()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(idle_away(control))
        for path in args:
            tg.create_task(control.unix_socket_client(path))


if __name__ == '__main__':
    asyncio.run(main())
