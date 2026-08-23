from a2a.types.a2a_pb2 import Task

from mcp_a2a_bridge.sqlite_task_store import SQLiteTaskStore


async def test_sqlite_task_store_survives_new_instance(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    first = SQLiteTaskStore(path)
    task = Task(id="task-1", context_id="ctx-1")
    await first.save(task, None)

    second = SQLiteTaskStore(path)
    assert await second.get("task-1", None) == task


async def test_sqlite_task_store_deletes_tasks(tmp_path):
    store = SQLiteTaskStore(tmp_path / "tasks.sqlite3")
    await store.save(Task(id="task-1"), None)
    await store.delete("task-1", None)

    assert await store.get("task-1", None) is None
