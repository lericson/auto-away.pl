"Asynchronous IRC client"

from __future__ import annotations


import re
import sys
import asyncio
import datetime
from typing import Callable, cast
from dataclasses import dataclass, field


LOG_LEVEL = 'debug'


def debug(*a: object) -> None:
    if LOG_LEVEL == 'debug':
        print(f'[{datetime.datetime.now()}]', *a, file=sys.stderr)


IRC_PAT = re.compile(r'''
    \s*
    # Time specification is apparently a thing
    (?:
        @time=
        (?P<ts>\d\d\d\d-\d\d-\d\dT\d\d:\d\d:\d\d.\d\d\dZ)
        \s+
    )?
    (?:
        :
        (?:(?P<nick>[^!@\s]+)!)?
        (?:(?P<user>[^@\s]+)@)?
        (?P<host>[^\s]+)
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

PatGroupsType = (tuple[str|None, None, None, None, str, str, str|None] |
                 tuple[str|None, None, None,  str, str, str, str|None] |
                 tuple[str|None, None,  str,  str, str, str, str|None] |
                 tuple[str|None,  str,  str,  str, str, str, str|None])


def selftest() -> None:
    def test(line: str, reference: tuple[str | None, ...]) -> None:
        match = re.match(IRC_PAT, line)
        groups: PatGroupsType | None = None
        if match is not None:
            groups = cast(PatGroupsType, match.groups())
        try:
            assert match is None or reference is not None
            assert groups == reference, (groups, reference)
        except AssertionError:
            print(f'{line = }')
            print(f'{match = }')
            print(f'{reference = }')
            print(f'   {groups = }')
            raise
    ts = '2023-10-02T06:27:18.020Z'
    test(f'@time={ts} :nick!user@host COMMAND arg :long text\r\n',
         (ts,   'nick', 'user', 'host', 'COMMAND', 'arg', 'long text'))
    test(f'@time={ts} :nick!user@host COMMAND     :long text\r\n',
         (ts,   'nick', 'user', 'host', 'COMMAND', '', 'long text'))
    test(f'@time={ts} :nick!user@host COMMAND arg           \r\n',
         (ts,   'nick', 'user', 'host', 'COMMAND', 'arg', None))
    test(f'@time={ts} :nick!user@host COMMAND arg           \r\n',
         (ts,   'nick', 'user', 'host', 'COMMAND', 'arg', None))
    test( '           :nick!user@host COMMAND arg           \r\n',
         (None, 'nick', 'user', 'host', 'COMMAND', 'arg', None))
    test( '                     :host COMMAND arg           \r\n',
         (None,   None,   None, 'host', 'COMMAND', 'arg', None))
    test( '                           COMMAND arg           \r\n',
         (None,   None,   None,   None, 'COMMAND', 'arg', None))
    test( '                           COMMAND               \r\n',
         (None,   None,   None,   None, 'COMMAND', '', None))
    test(':libera.proxy 333 lericson ##foo mawk!mawk@mawk 1690531926\r\n',
         (None, None, None, 'libera.proxy', '333', 'lericson ##foo mawk!mawk@mawk 1690531926', None))


selftest()


@dataclass
class Source:
    nick: str|None = None
    user: str|None = None
    host: str|None = None

    @property
    def is_server(self) -> bool:
        return not self.nick


def parse_line(line: str) -> tuple[Source, list[str]]:

    if (match := re.match(IRC_PAT, line)) is None:
        return Source(), ['parse_failed', line]

    groups: PatGroupsType = cast(PatGroupsType, match.groups())
    (_, nick, user, host, cmd, spargs, text) = groups

    cmd = cmd.lower()

    args = spargs.split() if spargs else []
    if text is not None:
        args.append(text)

    return Source(nick, user, host), [cmd, *args]


class Interrupt(asyncio.mixins._LoopBoundMixin):  # pylint: disable=protected-access

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

    my_nick:  str | None = None
    is_away: bool | None = None

    idle_interrupt: Interrupt = field(default_factory=Interrupt, repr=False)
    away_changed:   Interrupt = field(default_factory=Interrupt, repr=False)

    async def set_away(self, awayness: bool) -> None:
        awaycmd = b'AWAY :Auto-away\r\n' if awayness else b'AWAY\r\n'
        if self.is_away != awayness:
            self.writer.write(awaycmd)
            await self.away_changed.wait()
            assert self.is_away == awayness

    async def communicate(self, encoding='utf-8') -> None:
        """Main communication coroutine"""

        self.my_nick = 'e'
        self.is_away = False

        self.writer.write(b'USER a b c d\r\n')
        self.writer.write(f'NICK {self.my_nick}\r\n'.encode(encoding))

        await self.writer.drain()

        async for line_bytes in self.reader:

            try:
                line = line_bytes.decode('utf-8')
            except UnicodeDecodeError as e:
                print(line)
                print(f'{encoding} decode failed, falling back to latin1', file=sys.stderr)
                print(f'{e} on {line_bytes!r}', file=sys.stderr)
                line = line_bytes.decode('latin1')

            src, args = parse_line(line)

            match args:

                case 'nick', new_nick, *_ if src.nick == self.my_nick:
                    debug(self, f'{new_nick = }')
                    self.my_nick = new_nick

                case 'privmsg', *_ if src.nick == self.my_nick:
                    self.idle_interrupt.trigger()

                case '305', who, *_ if who == self.my_nick:
                    self.is_away = False
                    debug(self, f'{self.is_away = }')
                    self.away_changed.trigger()

                case '306', who, *_ if who == self.my_nick:
                    self.is_away = True
                    debug(self, f'{self.is_away = }')
                    self.away_changed.trigger()

            await self.writer.drain()
