"""Durable source-bound topic views; identities and memory approvals are separate."""
from __future__ import annotations

import hashlib
import json
import uuid

from .transcript_groups import validate_transcript_groups


def source_fingerprint(segments: list[dict]) -> str:
    sources = [{key: source.get(key) for key in ("id", "start", "end", "text")}
               for source in sorted(segments, key=lambda source: source["start"])]
    return hashlib.sha256(json.dumps(sources, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def topic_view(recording: dict, segments: list[dict], memories: list[dict]) -> list[dict]:
    topics = recording.get("topics", [])
    if not topics or recording.get("topics_source_fingerprint") != source_fingerprint(segments):
        return []
    try:
        groups = [{key: item[key] for key in ("id", "title", "summary", "source_segment_ids")} for item in topics]
        validate_transcript_groups(groups, segments)
        if any(type(item.get("version")) is not int or item["version"] < 1
               or type(item.get("edited")) is not bool for item in topics):
            return []
    except (ValueError, TypeError, KeyError):
        return []
    result = []
    for number, item in enumerate(topics, 1):
        refs = set(item["source_segment_ids"])
        ids = [memory["id"] for memory in memories if memory.get("recording_id") == recording["id"]
               and memory.get("segment_id") in refs and memory.get("status") not in {"deleted", "superseded"}]
        result.append({**item, "number": number, "memory_ids": ids})
    return result


def merge_topics(recording: dict, segments: list[dict], groups: list[dict], memories: list[dict]) -> list[dict]:
    groups = validate_transcript_groups(groups, segments)
    current = topic_view(recording, segments, memories)
    by_sources = {tuple(item["source_segment_ids"]): item for item in current}
    proposed = {tuple(item["source_segment_ids"]): item for item in groups}
    # An edited partition belongs to the user. A retry cannot repartition it or
    # silently replace a correction with a new model title/summary.
    selected = current if any(item["edited"] for item in current) else groups
    result = []
    for item in selected:
        refs = tuple(item["source_segment_ids"])
        old = by_sources.get(refs)
        proposal = proposed.get(refs, item)
        title, summary = ((old["title"], old["summary"]) if old and old["edited"]
                          else (proposal["title"], proposal["summary"]))
        ident = old["id"] if old else str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([recording["id"], refs])))
        changed = bool(old and (old["title"], old["summary"]) != (title, summary))
        result.append({"id": ident, "title": title, "summary": summary, "source_segment_ids": list(refs),
                       "version": old["version"] + int(changed) if old else 1,
                       "edited": old["edited"] if old else False})
    return topic_view({**recording, "topics": result, "topics_source_fingerprint": source_fingerprint(segments)},
                      segments, memories)
