import json

import pytest

from delta.core.model import Conversation, Node
from delta.core.store import Store, StoreError


def test_roundtrip_preserves_everything(tmp_path):
    store = Store(tmp_path)
    c = Conversation(title="对话")
    u = c.add_user("你好")
    a = c.add_assistant(u.id, status="complete", model="deepseek-reasoner")
    a.text = "回答\n\n带 **markdown**"
    a.reasoning = "推理"
    a.meta["finish_reason"] = "stop"
    b = c.add_user("分叉")
    c.set_pointer(a.id)
    c.set_preferred(u.id, a.id)
    c.ui = {"scroll": {"anchor": a.id, "offset": 42}, "draft": "草稿"}
    store.save(c)

    loaded = store.load(c.id)
    assert loaded.to_dict() == c.to_dict()
    assert loaded.pointer_id == a.id
    assert loaded.nodes[u.id].preferred_child == a.id
    assert loaded.ui["draft"] == "草稿"
    assert loaded.nodes[a.id].text == "回答\n\n带 **markdown**"


def test_list_is_sorted_and_includes_summary(tmp_path):
    store = Store(tmp_path)
    first = Conversation(title="first")
    first.add_user("one")
    store.save(first)
    second = Conversation(title="second")
    second.add_user("two")
    second.updated_at = first.updated_at + 10
    store.save(second)
    items = store.list()
    assert [i["id"] for i in items] == [second.id, first.id]
    assert items[0]["title"] == "second"
    assert items[0]["nodes"] == 1


def test_delete_moves_to_trash(tmp_path):
    store = Store(tmp_path)
    c = Conversation()
    c.add_user("q")
    store.save(c)
    store.delete(c.id)
    assert not store.path_for(c.id).exists()
    assert len(list(store.trash_dir.glob("*.json"))) == 1
    with pytest.raises(StoreError):
        store.load(c.id)


def test_corrupt_file_is_reported(tmp_path):
    store = Store(tmp_path)
    bad = store.conversations_dir / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(StoreError):
        store.load("bad")
    listed = store.list()
    assert listed[0]["corrupt"] is True


def test_settings_roundtrip(tmp_path):
    store = Store(tmp_path)
    assert store.read_settings() == {}
    store.update_settings(last_id="abc", geometry="xyz", atlas=False)
    assert store.read_settings() == {"last_id": "abc", "geometry": "xyz", "atlas": False}


def test_no_temp_files_left(tmp_path):
    store = Store(tmp_path)
    c = Conversation()
    c.add_user("q")
    store.save(c)
    store.save(c)
    leftovers = list(store.conversations_dir.glob("*.tmp"))
    assert leftovers == []


def test_crash_during_stream_normalizes_to_interrupted(tmp_path):
    store = Store(tmp_path)
    c = Conversation()
    u = c.add_user("q")
    a = c.add_assistant(u.id)
    a.text = "半句"
    assert a.status == "streaming"
    store.save(c)
    loaded = store.load(c.id)
    assert loaded.nodes[a.id].status == "interrupted"
    assert loaded.nodes[a.id].text == "半句"


def test_invalid_conversation_id_rejected(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(StoreError):
        store.path_for("../evil")
