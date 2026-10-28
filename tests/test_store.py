import json

import pytest

from delta.core.model import Conversation
from delta.core.store import Store, StoreError
from tests.helpers import add, reorder, root


def test_roundtrip_preserves_everything(tmp_path):
    store = Store(tmp_path)
    c = Conversation(title="对话")
    u = root(c, "你好")
    a = add(c, u.id, "assistant", "回答\n\n带 **markdown**", model="deepseek-reasoner")
    a.reasoning = "推理"
    a.meta["finish_reason"] = "stop"
    b = add(c, a.id, "user", "分叉")
    b2 = add(c, a.id, "user", "另一个分叉")
    reorder(c, a.id, b2.id, 0)
    c.set_baseline(a.id)
    c.ui = {"scroll": {"anchor": a.id, "offset": 42}, "slots": {"version": 1, "active": None, "items": []}}
    store.save(c)

    loaded = store.load(c.id)
    assert loaded.to_dict() == c.to_dict()
    assert loaded.baseline_id == a.id
    assert loaded.nodes[u.id].default_child == a.id
    assert loaded.display_ids(a.id) == [b2.id, b.id]
    assert [n.id for n in loaded.children(a.id)] == [b.id, b2.id]
    assert loaded.ui["slots"]["version"] == 1
    assert loaded.nodes[a.id].text == "回答\n\n带 **markdown**"
    assert loaded.nodes[a.id].default_child == b2.id


def test_list_is_sorted_and_includes_summary(tmp_path):
    store = Store(tmp_path)
    first = Conversation(title="first")
    root(first, "one")
    store.save(first)
    second = Conversation(title="second")
    root(second, "two")
    second.updated_at = first.updated_at + 10
    store.save(second)
    items = store.list()
    assert [i["id"] for i in items] == [second.id, first.id]
    assert items[0]["title"] == "second"
    assert items[0]["nodes"] == 1


def test_delete_moves_to_trash(tmp_path):
    store = Store(tmp_path)
    c = Conversation()
    root(c, "q")
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
    root(c, "q")
    store.save(c)
    store.save(c)
    leftovers = list(store.conversations_dir.glob("*.tmp"))
    assert leftovers == []


def test_crash_during_stream_normalizes_to_interrupted(tmp_path):
    store = Store(tmp_path)
    c = Conversation()
    u = root(c, "q")
    a = add(c, u.id, "assistant", status="streaming")
    a.text = "半句"
    assert a.status == "streaming"
    store.save(c)
    loaded = store.load(c.id)
    assert loaded.nodes[a.id].status == "interrupted"
    assert loaded.nodes[a.id].text == "半句"


def test_saved_document_is_schema_2(tmp_path):
    store = Store(tmp_path)
    c = Conversation()
    root(c, "q")
    path = store.save(c)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema"] == 2
    assert "baseline_id" in raw and "pointer_id" not in raw


def test_invalid_conversation_id_rejected(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(StoreError):
        store.path_for("../evil")
