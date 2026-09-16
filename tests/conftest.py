import fakeredis.aioredis
import pytest

from tests.helpers import FakeNotifier, make_config

pytest_plugins = []


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def config():
    return make_config()


@pytest.fixture
def notifier():
    return FakeNotifier()
