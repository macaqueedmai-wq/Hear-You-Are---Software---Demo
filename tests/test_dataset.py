"""Dataset intake must stay authorized, atomic and excluded from runtime."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from hya.dataset import DatasetNotFoundError, DatasetStore, MAX_BYTES, MAX_ITEMS


def item(**overrides):
    return {"kind": "guidance", "title": "虛構格式示例", "content": "這是自建的虛構格式示例，沒有臨床判斷或專業建議。",
            "source": "本測試自建的虛構資料", "license": "CC0-1.0", "deidentified": True,
            "consent_to_use": True, **overrides}


def jsonl(*items):
    return ("\n".join(json.dumps(value, ensure_ascii=False) for value in items) + "\n").encode("utf-8")


@pytest.fixture
def store(tmp_path):
    return DatasetStore(tmp_path / "data")


def test_import_is_pending_and_cannot_enable_runtime_or_training(store):
    raw = jsonl(item(status="approved", training_enabled=True, runtime_enabled=True,
                     professional_reviewer="名稱只是上傳方聲明"), item(kind="knowledge"))
    summary = store.import_file("example.jsonl", raw)
    assert summary["duplicate"] is False and summary["item_count"] == 2
    assert summary["kind_counts"] == {"guidance": 1, "knowledge": 1}
    assert summary["status"] == "pending_review"
    assert summary["training_enabled"] is False and summary["runtime_enabled"] is False
    detail = store.get(summary["id"])
    assert all(row["status"] == "pending_review" and not row["training_enabled"] and not row["runtime_enabled"] for row in detail["items"])
    assert detail["items"][0]["professional_reviewer"] == "名稱只是上傳方聲明"
    assert store.list()[0]["id"] == summary["id"]
    with sqlite3.connect(store.db_path) as database:
        assert database.execute("SELECT original_jsonl FROM datasets").fetchone()[0] == raw


@pytest.mark.parametrize("field", ["kind", "title", "content", "source", "license", "deidentified", "consent_to_use"])
def test_missing_required_field_is_rejected_without_partial_import(store, field):
    incomplete = item()
    del incomplete[field]
    with pytest.raises(ValueError, match="缺少"):
        store.import_file("missing.jsonl", jsonl(item(), incomplete))
    assert store.list() == []


@pytest.mark.parametrize("field", ["deidentified", "consent_to_use"])
@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_permission_requires_literal_true(store, field, value):
    with pytest.raises(ValueError, match="明確聲明"):
        store.import_file("unauthorized.jsonl", jsonl(item(**{field: value})))
    assert store.list() == []


@pytest.mark.parametrize("overrides", [
    {"kind": "diagnosis"}, {"kind": ["guidance"]}, {"title": " "}, {"content": 123},
    {"source": ""}, {"license": None}, {"professional_reviewer": True}, {"professional_reviewer": ""},
])
def test_invalid_kinds_and_field_types_are_rejected(store, overrides):
    with pytest.raises(ValueError):
        store.import_file("invalid.jsonl", jsonl(item(**overrides)))
    assert store.list() == []


@pytest.mark.parametrize("raw", [b"", b" \r\n\n", b"\xff", b"[]\n", b"42\n", b"{broken}\n"])
def test_invalid_encoding_empty_or_non_object_json_is_rejected(store, raw):
    with pytest.raises(ValueError):
        store.import_file("invalid.jsonl", raw)
    assert store.list() == []


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_nonfinite_json_constants_are_rejected_even_in_unused_fields(store, constant):
    raw = json.dumps(item(), ensure_ascii=False)[:-1] + ',"unused":' + constant + "}\n"
    with pytest.raises(ValueError, match="NaN 或 Infinity"):
        store.import_file("not-json.jsonl", raw.encode("utf-8"))
    assert store.list() == []


def test_duplicate_json_keys_cannot_make_consent_ambiguous(store):
    raw = json.dumps(item())[:-1] + ',"consent_to_use":false}\n'
    with pytest.raises(ValueError, match="重複欄位"):
        store.import_file("ambiguous.jsonl", raw.encode("utf-8"))
    assert store.list() == []


def test_limits_5mb_and_1000_items(store):
    with pytest.raises(ValueError, match="5 MB"):
        store.import_file("huge.jsonl", b"x" * (MAX_BYTES + 1))
    with pytest.raises(ValueError, match="1000"):
        store.import_file("many.jsonl", jsonl(*[item()] * (MAX_ITEMS + 1)))
    assert store.list() == []
    accepted = store.import_file("maximum.jsonl", jsonl(*[item()] * MAX_ITEMS))
    assert accepted["item_count"] == MAX_ITEMS


def test_utf8_bom_and_blank_lines_are_accepted(store):
    summary = store.import_file("bom.ndjson", b"\xef\xbb\xbf\n\n" + jsonl(item()) + b"\n")
    assert summary["item_count"] == 1


def test_hash_dedup_is_stable_across_names_and_store_instances(store):
    raw = jsonl(item())
    first = store.import_file("first.jsonl", raw)
    second = DatasetStore(store.data_dir).import_file("renamed.jsonl", raw)
    assert second["duplicate"] is True and second["id"] == first["id"]
    assert second["filename"] == "first.jsonl"
    assert len(store.list()) == 1
    with sqlite3.connect(store.db_path) as database:
        assert database.execute("SELECT COUNT(*) FROM dataset_entries").fetchone()[0] == 1


def test_simultaneous_duplicate_imports_do_not_create_more_evidence(store):
    raw = jsonl(item())
    stores = [DatasetStore(store.data_dir), DatasetStore(store.data_dir)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda source: source.import_file("same.jsonl", raw), stores))
    assert len({result["id"] for result in results}) == 1
    assert sum(result["duplicate"] is False for result in results) == 1
    assert len(store.list()) == 1


def test_user_filename_and_id_are_never_used_as_disk_paths(store, tmp_path):
    sentinel = tmp_path / "outside.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    summary = store.import_file("C:\\outside\\..\\..\\\u202eevidence.jsonl\n", jsonl(item()))
    assert summary["filename"] == "evidence.jsonl"
    assert all(path.is_relative_to(store.dataset_dir) for path in store.dataset_dir.rglob("*"))
    with pytest.raises(DatasetNotFoundError):
        store.delete("../../outside.txt")
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    with pytest.raises(ValueError, match="JSONL"):
        store.import_file("danger.exe", jsonl(item()))


def test_delete_removes_original_bytes_and_entries(store):
    summary = store.import_file("remove.jsonl", jsonl(item()))
    removed = store.delete(summary["id"])
    assert removed["deleted"] and removed["status"] == "deleted"
    assert not removed["training_enabled"] and not removed["runtime_enabled"]
    assert store.list() == []
    with pytest.raises(DatasetNotFoundError):
        store.get(summary["id"])
    with pytest.raises(DatasetNotFoundError):
        store.delete(summary["id"])
    with sqlite3.connect(store.db_path) as database:
        assert database.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM dataset_entries").fetchone()[0] == 0


def test_storage_failure_rolls_back_original_metadata_and_entries(store, monkeypatch):
    original_write = store._write_import

    def fail_after_write(database, summary, entries, raw):
        original_write(database, summary, entries, raw)
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(store, "_write_import", fail_after_write)
    with pytest.raises(OSError, match="storage failure"):
        store.import_file("failed.jsonl", jsonl(item()))
    assert store.list() == []
    with sqlite3.connect(store.db_path) as database:
        assert database.execute("SELECT COUNT(*) FROM dataset_entries").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0
    monkeypatch.setattr(store, "_write_import", original_write)
    assert store.import_file("retry.jsonl", jsonl(item()))["duplicate"] is False
