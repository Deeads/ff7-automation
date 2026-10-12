"""Reconnect transport and detect timeline changes without writing game memory."""
import time

from ff7_state import LiveRAM, StateError
from emulators import verified, KERNEL_OFFSET, KERNEL_SIZE
from ram_probe import read


class ConnectionPending(StateError):
    """A recoverable attachment failure; no input may be sent."""


class SessionRAM:
    def __init__(self, pid=None, emulator='auto', base=None, stopped=lambda: False,
                 factory=LiveRAM, clock=time.monotonic):
        self.requested_pid, self.emulator, self.requested_base = pid, emulator, base
        self.stopped, self.factory, self.clock = stopped, factory, clock
        self.source = None
        self.pid = pid
        self.epoch = 0
        self.retry_at = 0
        self.reason = 'Waiting for an emulator with FF7 USA loaded.'

    def _attach(self):
        if self.source is not None:
            return
        if self.stopped() or self.clock() < self.retry_at:
            raise ConnectionPending(self.reason)
        try:
            self.source = self.factory(self.requested_pid, emulator=self.emulator,
                                       base=self.requested_base, stopped=self.stopped)
            self.pid = self.source.pid
            self.epoch += 1
        except (OSError, StateError) as exc:
            self.reason = str(exc)
            self.retry_at = self.clock() + 2
            raise ConnectionPending(self.reason) from exc

    def _lost(self, exc):
        self.close()
        self.reason = f'Connection lost: {exc}. Waiting to reconnect.'
        self.retry_at = self.clock() + 2
        raise ConnectionPending(self.reason) from exc

    def raw(self):
        self._attach()
        try:
            data = self.source.raw()
        except OSError as exc:
            self._lost(exc)
        if not verified(data[KERNEL_OFFSET:KERNEL_OFFSET + KERNEL_SIZE]):
            self._lost(StateError('FF7 executable no longer matches this RAM allocation'))
        return data

    @property
    def handle(self):
        self._attach()
        return self.source.handle

    @property
    def base(self):
        self._attach()
        return self.source.base

    def snapshot(self, attempts=3):
        self._attach()
        try:
            state = self.source.snapshot(attempts=attempts)
            return dict(state, connection_epoch=self.epoch)
        except OSError as exc:
            self._lost(exc)
        except StateError as exc:
            # Valid FF7 RAM with an unsupported mode is a gameplay limitation,
            # not a broken connection. Only rediscover when the RAM disappeared.
            try:
                valid = verified(read(self.source.handle, self.source.base + KERNEL_OFFSET, KERNEL_SIZE))
            except OSError:
                valid = False
            if not valid:
                self._lost(exc)
            raise

    def close(self):
        source, self.source = self.source, None
        if source is not None:
            source.close()


class Timeline:
    """Forget transient plans on reconnect, rewind, and a loaded forward save.

    game_seconds is the FF7 savemap play timer, not the host clock. Normal
    progression/cutscenes do not advance it substantially faster than real time.
    """
    def __init__(self, clock=time.monotonic):
        self.last = None
        self.clock = clock

    def observe(self, state):
        now = self.clock()
        current = (state.get('connection_epoch'), state.get('game_seconds'),
                   state.get('story_progress'), now)
        previous, self.last = self.last, current
        if previous is None:
            return False
        if current[0] != previous[0]:
            return True
        if current[1] is not None and previous[1] is not None:
            delta = current[1] - previous[1]
            if delta < 0 or delta > max(30, (now - previous[3]) * 4 + 5):
                return True
        return (current[2] is not None and previous[2] is not None and current[2] < previous[2])
