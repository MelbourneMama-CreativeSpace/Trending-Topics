"""Phase 2: single-execution guard (PRD 71)."""

import os
import time

import pytest

from app.errors import BriefingError, ErrorCode
from app.locking import ExecutionLock


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "data" / "run.lock"


@pytest.mark.unit
def test_acquire_creates_the_lock_file(lock_path):
    with ExecutionLock(lock_path, run_id="run-1"):
        assert lock_path.exists()


@pytest.mark.unit
def test_lock_is_released_on_normal_exit(lock_path):
    with ExecutionLock(lock_path, run_id="run-1"):
        pass

    assert not lock_path.exists()


@pytest.mark.unit
def test_lock_is_released_when_the_body_raises(lock_path):
    """PRD 90: a crash must never orphan the lock and block every later morning."""
    with pytest.raises(ValueError):
        with ExecutionLock(lock_path, run_id="run-1"):
            raise ValueError("pipeline exploded")

    assert not lock_path.exists()


@pytest.mark.unit
def test_second_concurrent_run_is_rejected(lock_path):
    with ExecutionLock(lock_path, run_id="run-1"):
        with pytest.raises(BriefingError) as caught:
            ExecutionLock(lock_path, run_id="run-2").acquire()

        assert caught.value.code == ErrorCode.RUN_ALREADY_IN_PROGRESS


@pytest.mark.unit
def test_lock_can_be_reacquired_after_release(lock_path):
    with ExecutionLock(lock_path, run_id="run-1"):
        pass

    with ExecutionLock(lock_path, run_id="run-2"):
        assert lock_path.exists()


@pytest.mark.unit
def test_lock_records_the_owning_run(lock_path):
    """Operator-facing: the file tells you which run is holding things up."""
    with ExecutionLock(lock_path, run_id="20260901-073000-a81f"):
        contents = lock_path.read_text(encoding="utf-8")

    assert "run_id=20260901-073000-a81f" in contents
    assert "pid=" + str(os.getpid()) in contents


@pytest.mark.unit
def test_stale_lock_is_broken(lock_path, run_log):
    """A process killed mid-run must not block every later morning forever."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("run_id=dead-run\n", encoding="utf-8")
    stale = time.time() - 3600
    os.utime(lock_path, (stale, stale))

    with ExecutionLock(lock_path, run_id="run-2", ttl_seconds=900):
        assert lock_path.exists()

    assert any("STALE_LOCK_CLEARED" in message for message in run_log.messages)


@pytest.mark.unit
def test_fresh_lock_is_not_broken(lock_path):
    """A healthy slow run must not have its lock stolen out from under it."""
    with ExecutionLock(lock_path, run_id="run-1", ttl_seconds=900):
        with pytest.raises(BriefingError):
            ExecutionLock(lock_path, run_id="run-2", ttl_seconds=900).acquire()


@pytest.mark.unit
def test_release_does_not_delete_a_lock_we_never_held(lock_path):
    """The loser of a race must not remove the winning run lock on its way out."""
    holder = ExecutionLock(lock_path, run_id="run-1")
    holder.acquire()

    loser = ExecutionLock(lock_path, run_id="run-2")
    with pytest.raises(BriefingError):
        loser.acquire()
    loser.release()

    assert lock_path.exists(), "the winning lock was deleted by the loser"
    holder.release()
