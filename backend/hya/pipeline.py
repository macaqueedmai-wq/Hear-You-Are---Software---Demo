"""Run model-assisted memory stages without giving a model database authority."""
from __future__ import annotations

from .memory_agents import AgentError, STAGES
from .engine import NotFoundError
from .transcript_groups import validate_transcript_groups


def understand_recording(engine, agents, recording_id: str) -> dict:
    snapshot = engine.agent_snapshot(recording_id)
    status = agents.status()
    report = {"status": "processing", "stage": "pending", "model": status.get("model"), "error": None,
              "stages": [{"stage": stage, "status": "pending"} for stage in STAGES]}

    def progress(stage, state):
        report["stage"] = stage
        next(item for item in report["stages"] if item["stage"] == stage)["status"] = state
        engine.update_pipeline(recording_id, {key: report[key] for key in ("status", "stage", "model", "error", "stages")})

    try:
        if not snapshot["segments"]:
            raise AgentError("A completed transcript is required before DeepSeek processing.")
        if not status.get("configured"):
            raise AgentError("DeepSeek memory stages are not configured; check the backend settings.")
        if status.get("stages", {}).get("raw_evidence", {}).get("configured"):
            progress("raw_evidence", "processing")
            report["raw_review"] = agents.review_raw(snapshot["recording"], snapshot["segments"])
            progress("raw_evidence", "completed")
        else:
            progress("raw_evidence", "skipped")
        progress("atomic", "processing")
        proposals = agents.extract(snapshot["recording"], snapshot["segments"])
        memories = engine.prepare_agent_memories(snapshot, proposals)
        progress("atomic", "completed")
        # Proposed facts participate in advisory stages but are committed only
        # after all stages succeed and the entire source snapshot is rechecked.
        context_count = max(0, 100 - len(memories))
        context = snapshot["memories"][-context_count:] if context_count else []
        candidates = {memory["id"]: memory for memory in context}
        candidates.update({memory["id"]: memory for memory in memories})
        report["context_truncated"] = len(context) < len(snapshot["memories"])
        candidates = list(candidates.values())
        progress("consolidation", "processing")
        report["consolidation"] = agents.consolidate(candidates)
        report["transcript_groups"] = validate_transcript_groups(
            agents.group_transcript(snapshot["recording"], snapshot["segments"], proposals), snapshot["segments"])
        progress("consolidation", "completed")
        progress("retrieval", "processing")
        query = "Review this recording in its emotional and social context; preserve uncertainty."
        ranked = agents.retrieve(query, candidates)
        report["retrieved_memory_ids"] = ranked
        progress("retrieval", "completed")
        progress("governance", "processing")
        report["governance"] = agents.govern(candidates, snapshot["settings"])
        progress("governance", "completed")
        report.update(status="completed", stage="completed", memory_ids=[m["id"] for m in memories],
                      advisory_only=True)
        return engine.apply_agent_memories(recording_id, memories, snapshot["revision"], report)
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error) if isinstance(error, (AgentError, ValueError)) else "Memory processing failed; check the backend service."
        for item in report["stages"]:
            if item["status"] == "processing":
                item.update(status="failed", error=report["error"])
        # Failed/stale proposals are never retained after source changes.
        for field in ("raw_review", "consolidation", "retrieved_memory_ids", "governance", "memory_ids", "transcript_groups"):
            report.pop(field, None)
        try:
            engine.update_pipeline(recording_id, report)
        except NotFoundError:
            pass
        raise


def organize_recording(engine, agents, recording_id: str) -> dict:
    """Organize existing sources/atomic facts without extracting memories again."""
    snapshot = engine.agent_snapshot(recording_id)
    report = {"status": "processing", "stage": "consolidation", "error": None}
    engine.update_topic_pipeline(recording_id, report)
    try:
        if not snapshot["segments"]:
            raise AgentError("A completed transcript is required before topic organization.")
        source_by_id = {source["id"]: source for source in snapshot["segments"]}
        context = []
        for memory in snapshot["memories"]:
            source = source_by_id.get(memory.get("segment_id"))
            if not source or memory.get("recording_id") != recording_id:
                continue
            quote = memory.get("source_quote")
            if not isinstance(quote, str) or not quote or quote not in source["text"]:
                quote = source["text"]
            context.append({"content": memory["content"], "quote": quote, "source_segment_id": source["id"]})
        groups = agents.group_transcript(snapshot["recording"], snapshot["segments"], context)
        return engine.commit_topic_groups(recording_id, groups, snapshot["revision"], report)
    except Exception as error:
        report.update(status="failed", error=str(error) if isinstance(error, ValueError)
                      else "Topic organization failed; check the backend service.")
        try:
            engine.update_topic_pipeline(recording_id, report)
        except NotFoundError:
            pass
        raise
