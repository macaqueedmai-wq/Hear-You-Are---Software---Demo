"""Offline integration checks for model proposals and persisted source authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json

import pytest

from hya import engine as engine_module
from hya.engine import Engine, HK, NotFoundError, VersionConflict
from hya.memory_agents import AgentError, STAGES
from hya.pipeline import understand_recording


NOW = datetime(2026, 10, 18, 16, 0, tzinfo=HK)
SOURCE_TEXT = "I felt nervous before the presentation."
CONTENT = "The speaker felt nervous before the presentation."
ADVISORY_FIELDS = {"raw_review", "consolidation", "retrieved_memory_ids", "governance", "memory_ids", "transcript_groups"}
CALL_ORDER = ("raw_evidence", "atomic", "consolidation", "transcript_groups", "retrieval", "governance")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "_now", lambda: NOW)
    return Engine(tmp_path / "data", model_mode=True)


def add_recording(engine, *, suffix="primary", recorded_at=NOW.isoformat(), confirmed=False,
                  text=SOURCE_TEXT, extract=False):
    audio = engine.audio_dir / f"{suffix}.wav"
    audio.write_bytes(b"fictional-original-audio-for-pipeline-tests")
    recording = engine.create_recording(audio.name, audio, f"test-hash-{suffix}", recorded_at)
    engine.ingest_segments(recording["id"], [{
        "start": 2.3456, "end": 8.9012, "text": text, "speaker": "speaker_0",
        "words": [{"start": 2.3456, "end": 2.7, "word": "I"}],
    }], subject_confirmed=confirmed, extract=extract)
    return engine.get_recording(recording["id"]), audio


class FakeAgents:
    """Deterministic stage results; hooks simulate edits while a request is out."""

    def __init__(self, *, hook=None, fail=None, proposal_changes=None, content=CONTENT,
                 configured=True, raw_configured=True, groups=None):
        self.hook = hook
        self.fail = fail
        self.proposal_changes = proposal_changes or {}
        self.content = content
        self.configured = configured
        self.raw_configured = raw_configured
        self.groups = groups
        self.calls = []
        self.inputs = {}

    def status(self):
        return {"configured": self.configured, "model": "fictional-deepseek-model",
                "stages": {"raw_evidence": {"configured": self.raw_configured}}}

    def _stage(self, name, *inputs):
        self.calls.append(name)
        self.inputs[name] = deepcopy(inputs)
        if self.hook:
            self.hook(name, *inputs)
        if self.fail == name:
            raise AgentError(f"Fictional {name} stage failed; no proposals were applied.")

    def review_raw(self, recording, segments):
        self._stage("raw_evidence", recording, segments)
        return {"issues": [{"source_segment_id": segments[0]["id"], "reason": "Review the source in context."}]}

    def extract(self, recording, segments):
        self._stage("atomic", recording, segments)
        return [{"source_segment_id": segments[0]["id"], "quote": segments[0]["text"],
                 "content": self.content, "category": "mood", "memory_type": "episodic",
                 "scope": "recorded_conversation", **self.proposal_changes}]

    def consolidate(self, memories):
        self._stage("consolidation", memories)
        return {"proposals": []}

    def group_transcript(self, recording, segments, atomic_proposals):
        self._stage("transcript_groups", recording, segments, atomic_proposals)
        return deepcopy(self.groups) if self.groups is not None else [{
            "id": "topic-1", "title": "Presentation context", "summary": "The speaker described presentation preparation.",
            "source_segment_ids": [segment["id"] for segment in segments],
        }]

    def retrieve(self, query, memories):
        self._stage("retrieval", query, memories)
        return [memory["id"] for memory in memories]

    def govern(self, memories, settings):
        self._stage("governance", memories, settings)
        return {"reviews": [{"memory_ids": [memory["id"] for memory in memories],
                             "reason": "Advisory only: review context before accepting the interpretation."}]}


def test_five_stages_commit_only_after_success_and_preserve_exact_provenance(engine):
    recording, audio = add_recording(engine)
    original = deepcopy(recording["segments"])

    def no_partial_commit(stage, *inputs):
        assert engine.state()["memories"] == []
        assert engine.get_recording(recording["id"])["segments"] == original

    agents = FakeAgents(hook=no_partial_commit)
    result = understand_recording(engine, agents, recording["id"])
    assert agents.calls == list(CALL_ORDER)
    report = result["memory_pipeline"]
    assert report["status"] == "completed"
    assert report["advisory_only"] is True
    assert report["transcript_groups"] == [{
        "id": "topic-1", "title": "Presentation context", "summary": "The speaker described presentation preparation.",
        "source_segment_ids": [original[0]["id"]],
    }]
    assert [item["stage"] for item in report["stages"]] == list(STAGES)
    assert all(item["status"] == "completed" for item in report["stages"])
    memory = engine.state()["memories"][0]
    assert memory["id"] == report["memory_ids"][0]
    assert memory["source_quote"] == SOURCE_TEXT
    assert memory["content"] == CONTENT
    assert memory["source_refs"] == [{
        "recording_id": recording["id"], "segment_id": original[0]["id"],
        "start": 2.3456, "end": 8.9012, "text": SOURCE_TEXT, "is_demo": False,
    }]
    assert memory["event_time"] == recording["recorded_at"]
    assert memory["extraction_mode"] == "deepseek"
    assert memory["evidence_kind"] == "ai_inference"
    assert memory["subject_id"] == "unknown" and memory["confirmed"] is False
    assert memory["reviewed"] is False and memory["status"] == "pending"
    assert memory["follow_up"] is None
    assert result["segments"] == original
    assert result["segments"][0]["speaker"] == "unknown"
    assert result["segments"][0]["speaker_label"] == "speaker_0"
    assert audio.read_bytes() == b"fictional-original-audio-for-pipeline-tests"


def test_identity_confirmation_preserves_inference_and_does_not_approve_meaning(engine):
    recording, _ = add_recording(engine)
    understand_recording(engine, FakeAgents(), recording["id"])
    memory = engine.state()["memories"][0]
    engine.confirm_recording(recording["id"], [recording["segments"][0]["id"]])
    current = engine.get_source(memory["id"])["memory"]
    assert current["subject_id"] == "user" and current["confirmed"] is True
    assert current["status"] == "active" and current["version"] == 2
    assert current["evidence_kind"] == "ai_inference"
    assert current["reviewed"] is False
    assert engine.state()["proactive"] == []
    assert engine.get_recording(recording["id"])["segments"][0]["speaker_label"] == "speaker_0"


def test_preconfirmed_speaker_does_not_preapprove_extracted_memory(engine):
    recording, _ = add_recording(engine, confirmed=True)
    understand_recording(engine, FakeAgents(), recording["id"])
    memory = engine.state()["memories"][0]
    assert memory["subject_id"] == "user" and memory["confirmed"] is True
    assert memory["reviewed"] is False and memory["evidence_kind"] == "ai_inference"


def test_undated_recording_never_inherits_import_or_model_date(engine):
    recording, _ = add_recording(engine, recorded_at=None)
    understand_recording(engine, FakeAgents(), recording["id"])
    memory = engine.state()["memories"][0]
    assert memory["event_time"] is None
    assert engine.state()["cards"] == []


@pytest.mark.parametrize("changes", [
    {"source_segment_id": "invented-source-id"},
    {"quote": "I felt happy before the presentation."},
    {"quote": SOURCE_TEXT.lower()},
    {"quote": ""},
])
def test_tampered_source_ids_or_quotes_reject_entire_commit(engine, changes):
    recording, audio = add_recording(engine)
    with pytest.raises(ValueError, match="unknown source|quote"):
        understand_recording(engine, FakeAgents(proposal_changes=changes), recording["id"])
    assert engine.state()["memories"] == []
    current = engine.get_recording(recording["id"])
    assert current["segments"] == recording["segments"]
    assert current["memory_pipeline"]["status"] == "failed"
    assert ADVISORY_FIELDS.isdisjoint(current["memory_pipeline"])
    assert audio.exists()


def test_proposed_authority_fields_cannot_override_backend_source_identity_time_or_review(engine):
    recording, _ = add_recording(engine)
    agents = FakeAgents(proposal_changes={
        "id": "model-chosen-storage-id", "recording_id": "invented-recording",
        "event_time": "2099-01-01T10:00:00+08:00", "subject_id": "user",
        "confirmed": True, "reviewed": True, "start": -100,
        "follow_up": {"allowed": True, "due_at": NOW.isoformat()},
    })
    understand_recording(engine, agents, recording["id"])
    memory = engine.state()["memories"][0]
    assert memory["id"] != "model-chosen-storage-id"
    assert memory["recording_id"] == recording["id"]
    assert memory["event_time"] == recording["recorded_at"]
    assert memory["subject_id"] == "unknown"
    assert memory["confirmed"] is False and memory["reviewed"] is False
    assert memory["follow_up"] is None
    assert memory["source_refs"][0]["start"] == 2.3456


@pytest.mark.parametrize("failed_stage", ["consolidation", "transcript_groups", "retrieval", "governance"])
def test_later_stage_failure_keeps_raw_recording_and_commits_no_partial_facts(engine, failed_stage):
    recording, audio = add_recording(engine)
    agents = FakeAgents(fail=failed_stage)
    with pytest.raises(AgentError, match=f"{failed_stage} stage failed"):
        understand_recording(engine, agents, recording["id"])
    assert engine.state()["memories"] == []
    assert audio.exists()
    current = engine.get_recording(recording["id"])
    assert current["segments"] == recording["segments"]
    report = current["memory_pipeline"]
    assert report["status"] == "failed"
    stage = "consolidation" if failed_stage == "transcript_groups" else failed_stage
    assert next(item for item in report["stages"] if item["stage"] == stage)["status"] == "failed"
    assert ADVISORY_FIELDS.isdisjoint(report)


def test_unexpected_stage_exception_is_not_persisted_as_private_provider_text(engine):
    recording, _ = add_recording(engine)
    def fail(stage, *inputs):
        if stage == "governance":
            raise RuntimeError("fictional-private-provider-body")
    with pytest.raises(RuntimeError):
        understand_recording(engine, FakeAgents(hook=fail), recording["id"])
    report = engine.get_recording(recording["id"])["memory_pipeline"]
    assert "fictional-private-provider-body" not in json.dumps(report)
    assert engine.state()["memories"] == []


@pytest.mark.parametrize("mutation", ["memory_edit", "memory_delete", "settings", "source_add", "source_confirm", "source_delete"])
def test_source_memory_or_settings_changes_during_network_cancel_commit(engine, mutation):
    context, _ = add_recording(engine, suffix="context", confirmed=True, extract=True, text="I felt calm today.")
    context_memory = engine.state()["memories"][0]
    recording, audio = add_recording(engine)

    def mutate(stage, *inputs):
        if stage != "governance":
            return
        if mutation == "memory_edit":
            engine.revise_memory(context_memory["id"], "I felt calm after the meeting.", context_memory["version"])
        elif mutation == "memory_delete":
            engine.delete_memory(context_memory["id"])
        elif mutation == "settings":
            engine.settings_patch({"quiet_mode": True})
        elif mutation == "source_add":
            engine.ingest_segments(recording["id"], [{"start": 9, "end": 10, "text": "A new source arrived."}], extract=False)
        elif mutation == "source_confirm":
            engine.confirm_recording(recording["id"])
        else:
            engine.delete_recording(recording["id"])

    expected = NotFoundError if mutation == "source_delete" else VersionConflict
    with pytest.raises(expected):
        understand_recording(engine, FakeAgents(hook=mutate), recording["id"])
    assert not any(memory["recording_id"] == recording["id"] for memory in engine.state()["memories"])
    if mutation == "source_delete":
        assert not audio.exists()
        assert all(item["id"] != recording["id"] for item in engine.state()["recordings"])
    else:
        assert audio.exists()
        report = engine.get_recording(recording["id"])["memory_pipeline"]
        assert report["status"] == "failed" and ADVISORY_FIELDS.isdisjoint(report)
    assert engine.get_recording(context["id"])["segments"][0]["text"] == "I felt calm today."


def test_retry_deduplicates_memories_versions_and_provenance(engine):
    recording, _ = add_recording(engine)
    first = understand_recording(engine, FakeAgents(), recording["id"])
    memory = engine.state()["memories"][0]
    second = understand_recording(engine, FakeAgents(), recording["id"])
    assert first["memory_pipeline"]["memory_ids"] == second["memory_pipeline"]["memory_ids"]
    assert len(engine.state()["memories"]) == 1
    source = engine.get_source(memory["id"])
    assert source["memory"]["version"] == 1
    assert len(source["versions"]) == 1
    assert source["memory"]["source_refs"] == memory["source_refs"]


def test_deleting_memory_clears_advisory_report_and_retry_cannot_resurrect_tombstone(engine):
    recording, audio = add_recording(engine)
    understand_recording(engine, FakeAgents(), recording["id"])
    memory = engine.state()["memories"][0]
    engine.delete_memory(memory["id"])
    cleared = engine.get_recording(recording["id"])["memory_pipeline"]
    assert cleared["status"] == "stale"
    assert ADVISORY_FIELDS.isdisjoint(cleared)
    assert CONTENT not in json.dumps(cleared)
    assert memory["id"] not in json.dumps(cleared)
    assert engine.state()["memories"] == [] and audio.exists()
    understand_recording(engine, FakeAgents(), recording["id"])
    assert engine.state()["memories"] == []
    with pytest.raises(NotFoundError):
        engine.get_source(memory["id"])


def test_retry_never_overwrites_or_duplicates_a_user_corrected_source_memory(engine):
    recording, _ = add_recording(engine, confirmed=True)
    understand_recording(engine, FakeAgents(), recording["id"])
    memory = engine.state()["memories"][0]
    corrected = engine.revise_memory(memory["id"], "I was prepared and calm.", memory["version"])
    understand_recording(engine, FakeAgents(content="A differently worded model interpretation."), recording["id"])
    rows = engine.state()["memories"]
    assert [item["id"] for item in rows] == [memory["id"]]
    assert rows[0]["content"] == corrected["content"]
    assert rows[0]["reviewed"] is True
    assert rows[0]["version"] == corrected["version"]


def test_semantic_card_review_during_network_cancels_without_dangling_proposal_ids(engine):
    recording, _ = add_recording(engine, confirmed=True)
    understand_recording(engine, FakeAgents(), recording["id"])
    original_memory = engine.state()["memories"][0]
    card = engine.state()["cards"][0]
    engine.apply_card_summaries(NOW.date().isoformat(), [{
        "category": card["category"], "memory_ids": card["memory_ids"],
        "title": "Presentation review", "summary": "You described feeling nervous before the presentation.",
    }], card["versions"])
    card = engine.state()["cards"][0]

    def approve_during_request(stage, *inputs):
        if stage == "governance":
            engine.confirm_card(card["id"], card["versions"], card["review_token"])

    with pytest.raises(VersionConflict, match="changed during DeepSeek processing"):
        understand_recording(engine, FakeAgents(
            hook=approve_during_request, content="A different model interpretation of the same source."
        ), recording["id"])
    rows = engine.state()["memories"]
    assert len(rows) == 1 and rows[0]["id"] == original_memory["id"]
    assert rows[0]["version"] == original_memory["version"]  # Review itself does not increment versions.
    assert rows[0]["reviewed"] is True
    report = engine.get_recording(recording["id"])["memory_pipeline"]
    assert report["status"] == "failed"
    assert ADVISORY_FIELDS.isdisjoint(report)


def test_deleting_referenced_context_clears_other_recordings_advisory_report(engine):
    context, _ = add_recording(engine, suffix="context", confirmed=True, extract=True, text="I felt calm today.")
    context_memory = engine.state()["memories"][0]
    recording, _ = add_recording(engine)
    understand_recording(engine, FakeAgents(), recording["id"])
    report = engine.get_recording(recording["id"])["memory_pipeline"]
    assert context_memory["id"] in report["retrieved_memory_ids"]
    engine.delete_recording(context["id"])
    report = engine.get_recording(recording["id"])["memory_pipeline"]
    assert report["status"] == "stale"
    assert ADVISORY_FIELDS.isdisjoint(report)
    assert context_memory["id"] not in json.dumps(report)


def test_model_mode_pending_card_requires_llm_summary_before_semantic_confirmation(engine):
    recording, _ = add_recording(engine, confirmed=True)
    understand_recording(engine, FakeAgents(), recording["id"])
    card = engine.state()["cards"][0]
    assert card["analysis_mode"] == "pending"
    assert card["review_status"] == "pending"
    assert "Daily summary has not yet been generated" in card["summary"]
    with pytest.raises(ValueError, match="Generate the DeepSeek card"):
        engine.confirm_card(card["id"], card["versions"], card["review_token"])
    assert engine.state()["memories"][0]["reviewed"] is False
    engine.apply_card_summaries(NOW.date().isoformat(), [{
        "category": card["category"], "memory_ids": card["memory_ids"],
        "title": "Presentation nerves", "summary": "You described feeling nervous before your presentation.",
    }], card["versions"])
    generated = engine.state()["cards"][0]
    assert generated["analysis_mode"] == "llm"
    assert generated["review_token"] != card["review_token"]
    engine.confirm_card(generated["id"], generated["versions"], generated["review_token"])
    reviewed = engine.state()["memories"][0]
    assert reviewed["reviewed"] is True
    assert reviewed["evidence_kind"] == "ai_inference"


def test_model_mode_never_exposes_local_proactive_template_when_llm_text_is_missing(engine):
    recording, _ = add_recording(engine, confirmed=True, text="I need help preparing my presentation.")
    understand_recording(engine, FakeAgents(content="I need help preparing my presentation."), recording["id"])
    memory = engine.state()["memories"][0]
    engine.revise_memory(memory["id"], memory["content"], memory["version"])
    evaluation = engine.evaluate()
    assert evaluation["cards"]
    candidate = evaluation["cards"][0]
    assert candidate["generation_mode"] == "local_templates"
    assert engine.state()["proactive"] == []
    engine.apply_proactive_text(candidate["id"], "Prepare a small opening", "Would it help to rehearse your opening once?", candidate["versions"])
    shown = engine.state()["proactive"]
    assert len(shown) == 1 and shown[0]["generation_mode"] == "llm"
    assert shown[0]["summary"] == "Would it help to rehearse your opening once?"


def test_unconfigured_memory_stages_never_process_or_mutate_raw_sources(engine):
    recording, audio = add_recording(engine)
    agents = FakeAgents(configured=False)
    with pytest.raises(AgentError, match="not configured"):
        understand_recording(engine, agents, recording["id"])
    assert agents.calls == []
    assert engine.state()["memories"] == []
    assert engine.get_recording(recording["id"])["segments"] == recording["segments"]
    assert audio.exists()


def test_topic_groups_span_speaker_turns_and_cover_sources_without_atomic_proposals(engine):
    recording, _ = add_recording(engine)
    engine.ingest_segments(recording["id"], [
        {"start": 9, "end": 12, "text": "A friend helped me practice the presentation.", "speaker": "speaker_1", "words": []},
        {"start": 13, "end": 16, "text": "We walked home after class.", "speaker": "speaker_0", "words": []},
    ], extract=False)
    original = deepcopy(engine.get_recording(recording["id"])["segments"])
    groups = [
        {"id": "preparation", "title": "Preparing for a presentation", "summary": "Nerves and rehearsal support.",
         "source_segment_ids": [segment["id"] for segment in original[:2]]},
        {"id": "after-class", "title": "After class", "summary": "A walk home was described.",
         "source_segment_ids": [original[2]["id"]]},
    ]
    agents = FakeAgents(groups=groups)
    result = understand_recording(engine, agents, recording["id"])
    assert result["memory_pipeline"]["transcript_groups"] == groups
    assert result["segments"] == original
    assert all(not segment["confirmed"] and segment["subject_id"] == "unknown" for segment in result["segments"])
    # The source with no extracted memory still appears in the exhaustive group partition.
    assert len(engine.state()["memories"]) == 1
    assert all(not memory["reviewed"] and memory["status"] == "pending" for memory in engine.state()["memories"])
    _, group_sources, atomic_context = agents.inputs["transcript_groups"]
    assert group_sources == original and atomic_context[0]["quote"] == SOURCE_TEXT


@pytest.mark.parametrize("kind", ["unknown", "omitted", "duplicate", "reordered", "authority"])
def test_invalid_group_partition_cancels_every_proposal_and_keeps_sources(engine, kind):
    recording, audio = add_recording(engine)
    engine.ingest_segments(recording["id"], [{"start": 10, "end": 12, "text": "Another topic."}], extract=False)
    original = deepcopy(engine.get_recording(recording["id"])["segments"])
    refs = [segment["id"] for segment in original]
    group = {"id": "topic", "title": "Topic", "summary": "Provisional context.", "source_segment_ids": refs}
    if kind == "unknown":
        group["source_segment_ids"] = [refs[0], "invented"]
    elif kind == "omitted":
        group["source_segment_ids"] = refs[:1]
    elif kind == "duplicate":
        group["source_segment_ids"] = refs + refs[:1]
    elif kind == "reordered":
        group["source_segment_ids"] = refs[::-1]
    else:
        group["confirmed"] = True
    with pytest.raises(ValueError, match="Transcript group"):
        understand_recording(engine, FakeAgents(groups=[group]), recording["id"])
    current = engine.get_recording(recording["id"])
    assert current["segments"] == original and audio.exists()
    assert current["memory_pipeline"]["status"] == "failed"
    assert ADVISORY_FIELDS.isdisjoint(current["memory_pipeline"])
    assert engine.state()["memories"] == []
