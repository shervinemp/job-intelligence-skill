"""F5 — cross-process per-jid lock (ALGORITHMS.md Part 6).

A live `act --submit` and a shadow worker can race on the same jid: the
one-shot guard is per-jid state but not cross-process locked. The JidLock
serializes the guard-check + click + outcome for one jid, and reaps locks
left by crashed processes so a crash cannot wedge the job forever.
"""
import os
import sys
import time
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JI_HOME", os.path.expanduser("~/.ji"))


class JidLockTest(unittest.TestCase):
    def _lock(self, jid, **kw):
        from apply.common.jid_lock import JidLock
        return JidLock(jid, **kw)

    def test_acquire_release_cycle(self):
        lk = self._lock("0123456789abcdef")
        lk.acquire()
        self.assertTrue(lk._held)
        lk.release()
        self.assertFalse(lk._held)

    def test_blocks_second_acquirer_until_released(self):
        first = self._lock("1111111111111111")
        first.acquire()
        result = {}
        t = threading.Thread(target=self._acquire_in_thread,
                             args=("1111111111111111", result), daemon=True)
        t.start()
        time.sleep(0.4)
        # The second acquirer must be BLOCKED, not succeeding immediately.
        self.assertNotIn("ok", result)
        first.release()
        t.join(timeout=5.0)
        self.assertTrue(result.get("ok") is True)

    def _acquire_in_thread(self, jid, result):
        try:
            with self._lock(jid, timeout=5.0):
                result["ok"] = True
        except Exception as e:
            result["ok"] = type(e).__name__

    def test_stale_lock_is_reaped(self):
        """A lock older than the TTL (crashed process, no cleanup) must be
        reaped so the job is not wedged forever."""
        lk = self._lock("2222222222222222")
        lk.acquire()
        # Age the lock past the TTL.
        old = time.time() - 400
        os.utime(lk._path, (old, old))
        with self._lock("2222222222222222", timeout=3.0) as lk2:
            self.assertTrue(lk2._held)

    def test_dead_owner_lock_is_reaped(self):
        """A lock whose owning PID no longer exists must be reaped even if
        younger than the TTL — a crashed submit must not wedge the job."""
        from apply.common.jid_lock import JidLock, _pid_alive
        # A high reserved PID that never exists as a live process.
        dead_pid = 2 ** 20
        self.assertFalse(_pid_alive(dead_pid))
        lk = self._lock("4444444444444444")
        lk.acquire()
        # Write a dead PID into the lock file (impersonate a crashed owner).
        with open(lk._path, "w") as f:
            f.write(str(dead_pid))
        with self._lock("4444444444444444", timeout=3.0) as lk2:
            self.assertTrue(lk2._held)

    def test_short_jid_hashed_not_refused(self):
        """A short/test jid must still get a lock (hashed), never crash a
        submit — but an empty jid is refused."""
        lk = self._lock("abc")
        lk.acquire()
        self.assertTrue(lk._held)
        lk.release()
        with self.assertRaises(ValueError):
            self._lock("")

    def test_context_manager_releases(self):
        with self._lock("3333333333333333") as lk:
            self.assertTrue(lk._held)
        self.assertFalse(lk._held)


if __name__ == "__main__":
    unittest.main()
