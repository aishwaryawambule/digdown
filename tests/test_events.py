import asyncio

import pytest

from digdown.events import EventBus


@pytest.mark.anyio
async def test_subscriber_receives_published_event():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish({"type": "incident_created", "incident": {"id": 1}})
    event = await asyncio.wait_for(q.get(), timeout=1)
    assert event["incident"]["id"] == 1
    bus.unsubscribe(q)


@pytest.mark.anyio
async def test_unsubscribed_queue_gets_no_events():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish({"type": "x"})
    assert q.empty()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_publish_from_worker_thread_delivers_on_loop():
    bus = EventBus()
    bus.bind_loop(asyncio.get_running_loop())
    q = bus.subscribe()
    await asyncio.to_thread(bus.publish, {"type": "incident_updated", "n": 1})
    event = await asyncio.wait_for(q.get(), timeout=1)
    assert event["n"] == 1
    bus.unsubscribe(q)
