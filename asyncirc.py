
from __future__ import annotations


import re
import sys
import asyncio
import datetime
from typing import Callable
from dataclasses import dataclass, field


if not 'debug':
    def debug(*a: object) -> None:
        print(f'[{datetime.datetime.now()}]', *a, file=sys.stderr)
else:
    debug = lambda *a, **kw: None


pat = re.compile(r'''
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
''', re.VERBOSE)


def selftest() -> None:
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
        groups: tuple[str | None, ...] | None = None
        if match is not None:
            groups = match.groups()
        try:
            assert match is None or reference is not None
            assert reference is None or groups == reference
        except AssertionError:
            print(f'{line = }')
            print(f'{reference = }')
            print(f'{match = }')
            print(f'{groups = }')
            raise


selftest()


class Interrupt(asyncio.mixins._LoopBoundMixin):

    _waiters: list[asyncio.Future[None]]
    _get_loop: Callable[[], asyncio.AbstractEventLoop]

    def __init__(self) -> None:
        super().__init__()
        self._waiters = []

    def __repr__(self) -> str:
        res = super().__repr__()
        return f'<{res[1:-1]} [waiters:{len(self._waiters)}]>'

    def trigger(self) -> None:
        for fut in self._waiters:
            if not fut.done():
                fut.set_result(None)

    async def wait(self, *, timeout: float | None = None) -> bool:
        fut: asyncio.Future[None] = self._get_loop().create_future()
        self._waiters.append(fut)
        aw = asyncio.wait_for(fut, timeout=timeout)
        try:
            await aw
        except TimeoutError:
            return False
        else:
            return True
        finally:
            self._waiters.remove(fut)


@dataclass
class Client:
    reader: asyncio.StreamReader = field(repr=False)
    writer: asyncio.StreamWriter = field(repr=False)

    my_nick: str | None = None
    is_away: bool | None = None

    idle_interrupt: Interrupt = field(default_factory=Interrupt, repr=False)
    away_changed:   Interrupt = field(default_factory=Interrupt, repr=False)

    async def set_away(self, awayness: bool) -> None:
        awaycmd = b'AWAY :Auto-away\r\n' if awayness else b'AWAY\r\n'
        if self.is_away != awayness:
            self.writer.write(awaycmd)
            await self.away_changed.wait()
            assert self.is_away == awayness

    async def communicate(self) -> None:

        self.my_nick = 'e'
        self.is_away = False

        self.writer.write(b'USER a b c d\r\n')
        self.writer.write(f'NICK {self.my_nick}\r\n'.encode('utf-8'))

        await self.writer.drain()

        async for line_bytes in self.reader:

            try:
                line = line_bytes.decode('utf-8')
            except UnicodeDecodeError as e:
                print(f'line is not utf-8: {line!r}', file=sys.stderr)
                print('falling back to latin1', file=sys.stderr)
                print(e, file=sys.stderr)
                line = line_bytes.decode('latin1')

            match = re.match(pat, line)
            if match is None:
                print(f'pat match failed, {line = }', file=sys.stderr)
                continue

            groups: tuple[str, ...] = match.groups()
            (ts, addr, nick, user, host, cmd, spargs, text) = groups

            cmd = cmd.lower()

            args = spargs.split() if spargs else []
            if text is not None:
                args.append(text)

            match (cmd, *args):

                case 'nick', new_nick if nick == self.my_nick:
                    self.my_nick = new_nick
                    debug(self, f'{self.my_nick = }')

                case 'privmsg', _, _ if nick == self.my_nick:
                    self.idle_interrupt.trigger()

                case '305', who, _ if who == self.my_nick:
                    self.is_away = False
                    debug(self, f'{self.is_away = }')
                    self.away_changed.trigger()

                case '306', who, _ if who == self.my_nick:
                    self.is_away = True
                    debug(self, f'{self.is_away = }')
                    self.away_changed.trigger()

            await self.writer.drain()


