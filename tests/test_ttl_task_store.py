from mcp_a2a_bridge.ttl_task_store import TTLTaskStore
from a2a.types.a2a_pb2 import Task


async def test_task_store_evicts_least_recently_used_task():
    store = TTLTaskStore(maxsize=1)
    first = Task(id="first")
    second = Task(id="second")

    await store.save(first, None)
    await store.save(second, None)

    assert await store.get("first", None) is None
    assert await store.get("second", None) == second
