"""One diagnosis runs at a time, so what goes first matters: a container that's down
shouldn't wait behind a warning about a disk that's filling up."""
import asyncio

import pytest

from digdown.api import PriorityGate, diagnosis_priority
from digdown.models import Incident, Signal, SignalType


def _incident(kind, target="web"):
    signal = Signal(source="docker", type=kind, target=target)
    return Incident(source="docker", target=target, signal=signal, dedup_key=signal.dedup_key)


@pytest.mark.parametrize("down", [SignalType.CRASH, SignalType.OOM, SignalType.RESTART_LOOP, SignalType.UNHEALTHY])
@pytest.mark.parametrize("warning", [SignalType.DISK_FULL, SignalType.MEMORY_PRESSURE, SignalType.SLOW_RESPONSE,
                                     SignalType.LOG_ERROR_SPIKE])
def test_a_down_container_outranks_a_warning(down, warning):
    assert diagnosis_priority(_incident(down)) < diagnosis_priority(_incident(warning))


def test_a_stopped_container_comes_after_a_failing_one():
    # It may well be off on purpose.
    assert diagnosis_priority(_incident(SignalType.CRASH)) < diagnosis_priority(_incident(SignalType.STOPPED))


def test_every_kind_of_signal_has_a_priority():
    for kind in SignalType:
        assert isinstance(diagnosis_priority(_incident(kind)), int)


@pytest.mark.anyio
async def test_waiters_go_in_priority_order_then_arrival_order():
    gate = PriorityGate()
    order = []
    release_first = asyncio.Event()

    async def job(name, priority, hold=None):
        async with gate.slot(priority):
            order.append(name)
            if hold:
                await hold.wait()

    first = asyncio.create_task(job("running disk warning", 9, release_first))
    await asyncio.sleep(0)
    tasks = [asyncio.create_task(job(name, p)) for name, p in
             [("later warning", 9), ("crash a", 0), ("stopped", 5), ("crash b", 0)]]
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(first, *tasks)
    assert order == ["running disk warning", "crash a", "crash b", "stopped", "later warning"]


@pytest.mark.anyio
async def test_a_failed_job_frees_the_slot():
    gate = PriorityGate()
    with pytest.raises(RuntimeError):
        async with gate.slot(0):
            raise RuntimeError("boom")
    async with gate.slot(0):
        pass


@pytest.mark.anyio
async def test_a_cancelled_waiter_doesnt_block_the_queue():
    gate = PriorityGate()
    hold = asyncio.Event()
    done = []

    async def holder():
        async with gate.slot(0):
            await hold.wait()

    async def waiter(name, priority):
        async with gate.slot(priority):
            done.append(name)

    running = asyncio.create_task(holder())
    await asyncio.sleep(0)
    cancelled = asyncio.create_task(waiter("cancelled", 1))
    kept = asyncio.create_task(waiter("kept", 2))
    await asyncio.sleep(0)
    cancelled.cancel()
    await asyncio.sleep(0)
    hold.set()
    await asyncio.gather(running, kept)
    assert done == ["kept"]
