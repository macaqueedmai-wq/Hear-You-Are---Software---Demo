"""Validate advisory topic groups without changing their source transcript."""
from __future__ import annotations

import math
from numbers import Real
from typing import Any

MAX_TRANSCRIPT_SEGMENTS = 500
MAX_TRANSCRIPT_GROUPS = 100
_FIELDS = {"id", "title", "summary", "source_segment_ids"}


class TranscriptGroupError(ValueError):
    """Safe source-partition validation failure; never contains model text."""


def _text(value: Any, maximum: int, *, identifier: bool = False) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or identifier and value != value.strip()):
        raise TranscriptGroupError("Transcript groups contain invalid text or source IDs; no proposals were applied.")
    return value if identifier else value.strip()


def validate_transcript_groups(groups: Any, segments: list[dict]) -> list[dict]:
    """Return a strict exhaustive ordered partition of chronological source IDs.

    Each group cites a nonempty consecutive slice; flattening every group must
    equal the complete source order. Sorting copies the source list and never
    rewrites raw segment IDs, text, timing, speaker or confirmation fields.
    """
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_TRANSCRIPT_SEGMENTS:
        raise TranscriptGroupError("Transcript grouping requires a nonempty bounded transcript.")
    sources = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise TranscriptGroupError("Transcript grouping contains invalid source segments.")
        ident = _text(segment.get("id"), 200, identifier=True)
        start = segment.get("start")
        if isinstance(start, bool) or not isinstance(start, Real) or not math.isfinite(start) or start < 0:
            raise TranscriptGroupError("Transcript grouping requires valid chronological source times.")
        sources.append((start, ident))
    ordered_ids = [ident for _, ident in sorted(sources, key=lambda item: item[0])]
    if len(set(ordered_ids)) != len(ordered_ids):
        raise TranscriptGroupError("Transcript grouping contains repeated source IDs.")
    if not isinstance(groups, list) or not 1 <= len(groups) <= min(MAX_TRANSCRIPT_GROUPS, len(ordered_ids)):
        raise TranscriptGroupError("Transcript groups must be a nonempty bounded list.")
    result, group_ids, flattened = [], set(), []
    for raw in groups:
        if not isinstance(raw, dict) or set(raw) != _FIELDS:
            raise TranscriptGroupError("Transcript groups have an invalid schema; no proposals were applied.")
        ident = _text(raw["id"], 200, identifier=True)
        if ident in group_ids:
            raise TranscriptGroupError("Transcript groups contain repeated group IDs.")
        title, summary = _text(raw["title"], 120), _text(raw["summary"], 2000)
        refs = raw["source_segment_ids"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= len(ordered_ids):
            raise TranscriptGroupError("Transcript groups require nonempty source references.")
        refs = [_text(value, 200, identifier=True) for value in refs]
        group_ids.add(ident)
        flattened.extend(refs)
        result.append({"id": ident, "title": title, "summary": summary, "source_segment_ids": refs})
    if flattened != ordered_ids:
        raise TranscriptGroupError("Transcript groups must cover every source exactly once in chronological order.")
    return result
