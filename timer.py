from __future__ import annotations

import time
from abc import abstractmethod
from datetime import datetime, timedelta
from typing import Generic, Protocol, TypeVar, overload

T = TypeVar('T')
D = TypeVar('D')


class DeltaLike(Protocol):
    def __add__(self: D, other: D) -> D:
        pass

    def __sub__(self: D, other: D) -> D:
        pass

    def __neg__(self: D) -> D:
        pass

    def __lt__(self: D, other: D) -> bool:
        pass

    def __le__(self: D, other: D) -> bool:
        pass


class TimeLike(Protocol[D]):
    def __add__(self: T, other: D) -> T:
        pass

    @overload
    def __sub__(self: T, other: D) -> T:
        pass

    @overload
    def __sub__(self: T, other: T) -> D:
        pass

    def __lt__(self: T, other: T) -> bool:
        pass

    def __le__(self: T, other: T) -> bool:
        pass


DeltaT = TypeVar('DeltaT', bound=DeltaLike)
TimeT = TypeVar('TimeT',  bound=TimeLike)


class AbstractTimer(Generic[TimeT, DeltaT]):
    """A Timer is a started_at time, and a duration."""

    __slots__ = 'started_at', 'duration'

    started_at: TimeT
    duration:   DeltaT

    def __init__(self,
                 duration:   DeltaT,
                 *,
                 elapsed:    DeltaT | None = None,
                 starts_in:  DeltaT | None = None,
                 started_at:  TimeT | None = None) -> None:

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
        if name.startswith('__main__.'):
            name = tp.__name__
        if self.is_started:
            return (f'{name}({self.format_duration(self.remaining)}, '
                    f'elapsed={self.format_duration(self.elapsed)})')
        else:
            return (f'{name}({self.format_duration(self.remaining)}, '
                    f'starts_in={self.format_duration(self.starts_in)})')

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
        return self.eternity() <= self.expires

    @property
    def expires(self) -> TimeT:
        return self.started_at + self.duration

    @property
    def elapsed(self: AbstractTimer[TimeT, DeltaT]) -> DeltaT:
        return self.clock() - self.started_at

    @property
    def remaining(self) -> DeltaT:
        return self.duration - self.elapsed

    @property
    def starts_in(self) -> DeltaT:
        return -self.elapsed

    @classmethod
    def never(cls: type[AbstractTimerT]) -> AbstractTimerT:
        return cls(cls.eternity() - cls.clock())

    @staticmethod
    @abstractmethod
    def clock() -> TimeT:
        pass

    @staticmethod
    @abstractmethod
    def eternity() -> TimeT:
        pass

    @staticmethod
    @abstractmethod
    def format_duration(duration: DeltaT) -> str:
        pass


AbstractTimerT = TypeVar('AbstractTimerT', bound=AbstractTimer)


class Timer(AbstractTimer[float, float]):
    clock = time.monotonic
    eternity = staticmethod(lambda: float('inf'))
    format_duration = '{:.5g}'.format


class DatetimeTimer(AbstractTimer[datetime, timedelta]):
    clock = datetime.now
    eternity = staticmethod(lambda: datetime(3000, 1, 1, 0, 0, 0))
    format_duration = '{}'.format


def selftest() -> None:
    t: Timer = Timer.never()
    assert t.is_never
    assert not Timer(0.0).is_never
    assert Timer(0).is_expired
    assert DatetimeTimer(timedelta(minutes=2)).remaining
    assert '' != str(Timer(2))
    assert '' != DatetimeTimer(timedelta(minutes=2))


selftest()
