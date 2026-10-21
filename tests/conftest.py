import os
import tempfile
from pathlib import Path

import pytest
import nonebot
from pytest_asyncio import is_async_test
from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

if Path(".env.dev").exists():
    os.environ["ENVIRONMENT"] = "dev"
else:
    os.environ["ENVIRONMENT"] = "test"
    os.environ.setdefault("SNS_DATA", tempfile.mkdtemp(prefix="sns-subscribe-test-"))


def pytest_collection_modifyitems(items: list[pytest.Item]):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)


@pytest.fixture(scope="session", autouse=True)
async def after_nonebot_init(after_nonebot_init: None):
    driver = nonebot.get_driver()
    driver.register_adapter(OnebotV11Adapter)
    nonebot.load_from_toml("pyproject.toml")

    from nonebot_plugin_sns_subscribe.database import repository

    await repository.initialize()
