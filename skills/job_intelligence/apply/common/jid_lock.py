"""apply/common/jid_lock.py — cross-process per-job lock (F5).

A live `act --submit` and a shadow worker can race on the same jid: the
one-shot guard (submit_clicked) is per-jid state but not cross-process
locked, so both processes could read "not clicked" and both proceed. This
lock serializes the guard-check-and-click critical section per jid.

Implementation: a lockfile per jid in STATE_DIR, created with O_CREAT|O_EXCL
(atomic on POSIX and Windows), with stale-owner reaping (dead PID or TTL
expiry) so a crashed process does not wedge the job forever. Not a
replacement for the one-shot guard — a defense in depth that closes the
cross-process window.
"""
import os
import time

from lib.config import STATE_DIR

_LOCK_DIR = os.path.join(STATE_DIR, "jid_locks")
_LOCK_TTL_SEC = 300  # stale lock assumed dead after 5 min


def _pid_alive(pid):
    """True if the process `pid` exists on this platform."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


class JidLock:
    """Context manager acquiring an exclusive per-jid lock (cross-process).

        with jid_lock("abc123..."):
            ... guard check + click ...

    Blocks until acquired (polling the atomic create). Raises on timeout.
    """

    def __init__(self, jid, timeout=60.0):
        if not jid:
            raise ValueError("jid required for a lock identity")
        # Real jids are 16-hex; test/mock jids may be shorter. Normalize any
        # non-empty jid to a stable hash so the lock is unique per jid without
        # failing on short inputs (a submit must never crash on lock setup).
        if len(jid) < 8:
            import hashlib
            jid = hashlib.sha256(jid.encode()).hexdigest()[:16]
        self._path = os.path.join(_LOCK_DIR, f"{jid[:16]}.lock")
        self._timeout = timeout
        self._held = False

    def _try_acquire(self):
        os.makedirs(_LOCK_DIR, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            self._reap_if_stale()
            return False
        except OSError:
            return False

    def _reap_if_stale(self):
        """Delete the lock if its owner is gone (dead process) or it is older
        than the TTL (crashed without cleanup)."""
        try:
            # Dead-owner reap: if the locking PID no longer exists, the lock
            # is orphaned — a crashed process must not wedge the job.
            try:
                with open(self._path, "r") as f:
                    pid = int(f.read().strip() or "0")
                if pid and not _pid_alive(pid):
                    os.remove(self._path)
                    return
            except (OSError, ValueError):
                pass
            # Age-based reap: stale beyond the TTL.
            if time.time() - os.path.getmtime(self._path) > _LOCK_TTL_SEC:
                os.remove(self._path)
        except OSError:
            pass

    def acquire(self):
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            if self._try_acquire():
                self._held = True
                return
            time.sleep(0.2)
        raise TimeoutError(f"could not acquire per-jid lock for {self._path}")

    def release(self):
        if self._held:
            try:
                os.remove(self._path)
            except OSError:
                pass
            self._held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
