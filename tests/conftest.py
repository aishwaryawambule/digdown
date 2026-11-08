import pytest

from digdown.config import Config
from digdown.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "test.db"))


@pytest.fixture
def config():
    return Config(debounce_polls=1, restart_loop_threshold=3,
                  disk_percent_threshold=90.0)


class RecordingBus:
    """Stand-in for EventBus that just records published events."""
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


@pytest.fixture
def bus():
    return RecordingBus()


class FakeSource:
    """ContainerSource fake. Each call to list_states() returns the next frame;
    the last frame repeats once frames are exhausted."""
    def __init__(self, *frames):
        self.frames = list(frames)
        self.i = 0

    def list_states(self):
        idx = min(self.i, len(self.frames) - 1)
        self.i += 1
        return self.frames[idx]
