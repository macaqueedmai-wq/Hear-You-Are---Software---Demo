import type { MemoryPipeline, Segment, Topic } from "./types";

export interface TranscriptViewGroup {
  id: string;
  title: string;
  summary?: string;
  segments: Segment[];
  sourceIds: string[];
  speakerLabels: string[];
  mixedSpeakers: boolean;
  hasUnknownSpeaker: boolean;
  number?: number;
  topic?: Topic;
}

export function getSegmentId(segment: Segment): string | undefined {
  const value = segment.id || segment.segment_id;
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function isIdentityConfirmed(segment: Segment): boolean {
  return segment.confirmed === true || segment.subject_id === "user" || segment.speaker === "user";
}

export function pendingSegmentIds(segments: Segment[]): string[] {
  return [...new Set(segments.filter((segment) => !isIdentityConfirmed(segment))
    .map(getSegmentId).filter((id): id is string => Boolean(id)))];
}

export function sanitizeSelection(selected: string[], allowedIds: string[]): string[] {
  const selectedSet = new Set(selected);
  return [...new Set(allowedIds)].filter((id) => selectedSet.has(id));
}

export function toggleIds(selected: string[], targets: string[], checked: boolean, allowedIds: string[]): string[] {
  const next = new Set(sanitizeSelection(selected, allowedIds));
  const allowed = new Set(allowedIds);
  for (const id of targets) {
    if (checked && allowed.has(id)) next.add(id);
    else next.delete(id);
  }
  return sanitizeSelection([...next], allowedIds);
}

function speakerKey(segment: Segment): string {
  const label = segment.speaker_label;
  return label !== undefined && label !== null && String(label).trim() && String(label) !== "unknown"
    ? String(label) : "unknown";
}

function viewGroup(id: string, title: string, segments: Segment[], summary?: string): TranscriptViewGroup {
  const speakerLabels = [...new Set(segments.map(speakerKey))];
  return { id, title, summary, segments,
    sourceIds: [...new Set(segments.map(getSegmentId).filter((value): value is string => Boolean(value)))],
    speakerLabels, mixedSpeakers: speakerLabels.filter((label) => label !== "unknown").length > 1,
    hasUnknownSpeaker: speakerLabels.includes("unknown") };
}

function sourceTurns(segments: Segment[]): TranscriptViewGroup[] {
  const turns: Segment[][] = [];
  for (const segment of segments) {
    const previous = turns.at(-1);
    if (previous && speakerKey(previous[0]) === speakerKey(segment)) previous.push(segment);
    else turns.push([segment]);
  }
  return turns.map((turn, index) => viewGroup(`source-turn-${index}`, `Source turn ${index + 1}`, turn));
}

export function buildTranscriptView(segments: Segment[], pipeline?: MemoryPipeline | null): {
  kind: "topics" | "source"; reason: string; groups: TranscriptViewGroup[];
} {
  const fallback = (reason: string) => ({ kind: "source" as const, reason, groups: sourceTurns(segments) });
  if (pipeline?.status !== "completed") {
    return fallback(pipeline?.status === "processing" || pipeline?.status === "pending"
      ? "DeepSeek is organizing this transcript. Original source turns remain available below."
      : pipeline?.status === "failed" ? "Topic organization did not complete. Original source turns are shown below."
        : pipeline?.status === "stale" ? "Topic organization needs updating. Original source turns are shown below."
          : "Original source turns are shown while topics are organized automatically.");
  }
  const raw: unknown = pipeline.transcript_groups;
  if (raw === undefined) return fallback("Topics are not available yet. Original source turns remain available below.");
  const invalid = () => fallback("Topic links could not be matched to every original fragment. Original source turns remain available below.");
  const ids = segments.map(getSegmentId);
  if (!segments.length || ids.some((id) => !id) || new Set(ids).size !== ids.length
      || segments.some((segment, index) => !Number.isFinite(segment.start) || !Number.isFinite(segment.end)
        || segment.start < 0 || segment.end <= segment.start || (index > 0 && segment.start < segments[index - 1].start))) return invalid();
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > 100) return invalid();
  const groups: TranscriptViewGroup[] = [];
  const groupIds = new Set<string>();
  let cursor = 0;
  for (const value of raw) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
    const item = value as Record<string, unknown>;
    if (typeof item.id !== "string" || !item.id.trim() || item.id.length > 200 || groupIds.has(item.id)
        || typeof item.title !== "string" || !item.title.trim() || item.title.length > 120
        || typeof item.summary !== "string" || !item.summary.trim() || item.summary.length > 2000
        || !Array.isArray(item.source_segment_ids) || !item.source_segment_ids.length) return invalid();
    const refs = item.source_segment_ids;
    if (refs.some((id, index) => typeof id !== "string" || !id || id !== ids[cursor + index])) return invalid();
    groupIds.add(item.id);
    groups.push(viewGroup(item.id, item.title, segments.slice(cursor, cursor + refs.length), item.summary));
    cursor += refs.length;
  }
  return cursor === ids.length
    ? { kind: "topics", reason: "Topics organize related atomic memories. Original source words remain unchanged.", groups }
    : invalid();
}
