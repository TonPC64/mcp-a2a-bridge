from mcp_a2a_bridge.activity import ActivityLog, TEXT_PREVIEW_LIMIT


async def test_record_without_task_id_generates_one():
    log = ActivityLog()
    entry = await log.record(
        task_id=None, agent="planner", kind="send_message", state="working", text="hi"
    )
    assert entry.id
    assert entry.agent == "planner"


async def test_repeat_task_id_updates_existing_entry_instead_of_duplicating():
    log = ActivityLog()
    first = await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )
    second = await log.record(
        task_id="t1", agent="planner", kind="get_task", state="completed", text="done"
    )

    entries = await log.list()
    assert len(entries) == 1
    assert entries[0].state == "completed"
    assert entries[0].kind == "get_task"
    assert entries[0].created_at == first.created_at
    assert entries[0].updated_at == second.updated_at


async def test_text_is_truncated_to_limit():
    log = ActivityLog()
    long_text = "x" * (TEXT_PREVIEW_LIMIT + 50)
    entry = await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text=long_text
    )
    assert len(entry.text) == TEXT_PREVIEW_LIMIT


async def test_list_returns_newest_first():
    log = ActivityLog()
    await log.record(task_id="t1", agent="a", kind="send_message", state="working", text="one")
    await log.record(task_id="t2", agent="a", kind="send_message", state="working", text="two")

    entries = await log.list()
    assert [e.id for e in entries] == ["t2", "t1"]


async def test_updating_an_entry_moves_it_to_newest():
    log = ActivityLog()
    await log.record(task_id="t1", agent="a", kind="send_message", state="working", text="one")
    await log.record(task_id="t2", agent="a", kind="send_message", state="working", text="two")
    await log.record(task_id="t1", agent="a", kind="get_task", state="completed", text="done")

    entries = await log.list()
    assert [e.id for e in entries] == ["t1", "t2"]


async def test_eviction_drops_oldest_when_full():
    log = ActivityLog(maxsize=1)
    await log.record(task_id="t1", agent="a", kind="send_message", state="working", text="one")
    await log.record(task_id="t2", agent="a", kind="send_message", state="working", text="two")

    entries = await log.list()
    assert [e.id for e in entries] == ["t2"]