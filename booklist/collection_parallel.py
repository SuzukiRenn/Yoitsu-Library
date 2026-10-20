"""Cross-process pacing and bounded concurrency for coordinated detail workers."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError


@contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0'); stream.flush()
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                time.sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class PacedOpener:
    def __init__(self, opener, path, interval, *, clock=time.monotonic, sleep=time.sleep):
        self.opener, self.path, self.interval = opener, Path(path), interval
        self.clock, self.sleep = clock, sleep
        self.wait_seconds = 0

    def open(self, request, timeout):
        # Hold the lock through headers: a delayed worker cannot reserve a slot
        # then start after another worker. Response body reading can overlap.
        queued_at = self.clock()
        with exclusive(self.path.with_suffix('.lock')):
            if self.path.with_suffix('.stop').exists():
                raise RuntimeError('Coordinated collection stopped')
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            interval = max(10, self.interval(), state.get('interval', 10))
            wait = state.get('last_start', -1e9) + interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            if self.path.with_suffix('.stop').exists():
                raise RuntimeError('Coordinated collection stopped')
            self.wait_seconds += self.clock() - queued_at
            state.update(last_start=self.clock(), interval=interval)
            state['recent_starts'] = (state.get('recent_starts', []) + [{
                'monotonic':state['last_start'],'utc_epoch':time.time(),
                'pid':os.getpid(),'interval':interval}])[-128:]
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state), encoding='utf-8')
            os.replace(temporary, self.path)
            try:
                return self.opener.open(request, timeout=timeout)
            except HTTPError as error:
                if error.code in (401, 403, 429) or 300 <= error.code < 400:
                    self.path.with_suffix('.stop').touch()
                raise


def memory_available_mib():
    if os.name != 'nt':
        return None
    import ctypes
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in
                      ('total_phys','avail_phys','total_page','avail_page','total_virtual','avail_virtual','extended')]]
    status = Status(); status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.avail_phys // (1024*1024)


def concurrency_limit(available_mib, maximum=2):
    # Always allow one worker to finish the queue. Extra workers need headroom.
    if available_mib is None or available_mib < 768:
        return 1
    return max(1, min(maximum, int(available_mib)//384))
