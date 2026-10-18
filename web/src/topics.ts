import type { Memory, Recording, Topic } from "./types";
import { buildTranscriptView } from "./transcript.ts";

export function uploadingDateLabel(recording?: Pick<Recording, "created_at">): string {
  const value = recording?.created_at;
  if (!value || !Number.isFinite(new Date(value).getTime())) return "Uploading date unavailable";
  return `Uploading date · ${new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Hong_Kong",
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value))} · Hong Kong (UTC+8)`;
}

export function buildRecordingTranscriptView(recording: Recording) {
  const topics = recording.topics;
  if (Array.isArray(topics) && topics.length && topics.every((topic, index) =>
    Number.isInteger(topic.version) && topic.version >= 1 && typeof topic.edited === "boolean"
    && topic.number === index + 1 && Array.isArray(topic.memory_ids)
    && topic.memory_ids.every((id) => typeof id === "string" && id.length > 0))) {
    const result = buildTranscriptView(recording.segments || [], { status: "completed", transcript_groups: topics });
    if (result.kind === "topics") return { ...result, groups: result.groups.map((group, index) => ({
      ...group, number: topics[index].number, topic: topics[index],
    })) };
  }
  const fallback = buildTranscriptView(recording.segments || [], { status: recording.topic_pipeline?.status === "failed" ? "failed" : "processing" });
  return { ...fallback, groups: fallback.groups.map((group, index) => ({ ...group, number: index + 1 })) };
}

export function atomicTopicSections(recordings: Recording[], memories: Memory[]) {
  const visible = new Map(memories.map((memory) => [memory.id, memory]));
  const assigned = new Set<string>();
  const sections: { recording: Recording; topic?: Topic; number?: number; title: string; summary?: string; memories: Memory[] }[] = [];
  for (const recording of recordings) {
    for (const topic of recording.topics || []) {
      const linked = [...new Set(topic.memory_ids)].map((id) => visible.get(id))
        .filter((memory): memory is Memory => Boolean(memory && memory.recording_id === recording.id));
      linked.forEach((memory) => assigned.add(memory.id));
      if (linked.length) sections.push({ recording, topic, number: topic.number, title: topic.title, summary: topic.summary, memories: linked });
    }
    const remaining = memories.filter((memory) => memory.recording_id === recording.id && !assigned.has(memory.id));
    if (remaining.length) sections.push({ recording, title: "Atomic memories awaiting topic organization", memories: remaining });
  }
  return sections;
}

export function createTopicDraft(recording: Recording, topic: Topic, memories: Memory[]) {
  const current = new Map(memories.filter((memory) => memory.recording_id === recording.id
    && !["deleted", "superseded"].includes(memory.status)).map((memory) => [memory.id, memory]));
  return { recordingId: recording.id, recordingName: recording.filename, topicId: topic.id, version: topic.version,
    number: topic.number, title: topic.title, summary: topic.summary, savedTitle: topic.title, savedSummary: topic.summary,
    memories: [...new Set(topic.memory_ids)].map((id) => {
      const memory = current.get(id);
      return { id, version: memory?.version || 0, content: memory?.content || "", savedContent: memory?.content || "", unavailable: !memory };
    }) };
}
export type TopicEditDraft = ReturnType<typeof createTopicDraft>;

/** Refresh versions/baselines only; never erase a conflict draft or silently resubmit it. */
export function rebaseTopicDraft(draft: TopicEditDraft, recording: Recording, topic: Topic, memories: Memory[]): TopicEditDraft {
  const latest = createTopicDraft(recording, topic, memories);
  const previous = new Map(draft.memories.map((memory) => [memory.id, memory]));
  return { ...latest, title: draft.title, summary: draft.summary,
    memories: [...latest.memories.map((memory) => ({ ...memory, content: previous.get(memory.id)?.content ?? memory.content })),
      ...draft.memories.filter((memory) => !latest.memories.some((item) => item.id === memory.id)).map((memory) => ({ ...memory, unavailable: true }))] };
}
export function topicDraftChanged(draft: TopicEditDraft): boolean {
  return draft.title.trim() !== draft.savedTitle || draft.summary.trim() !== draft.savedSummary
    || draft.memories.some((memory) => memory.content.trim() !== memory.savedContent);
}
export function topicPatch(draft: TopicEditDraft) {
  if (!draft.title.trim() || !draft.summary.trim()) throw new Error("Enter a topic title and summary.");
  if (draft.title.trim().length > 120 || draft.summary.trim().length > 2000) throw new Error("The topic title or summary is too long.");
  if (draft.memories.some((memory) => memory.unavailable)) throw new Error("Some atomic memories are no longer available. Your draft is preserved; copy it before closing or refresh again.");
  const changed = draft.memories.filter((memory) => memory.content.trim() !== memory.savedContent);
  if (changed.some((memory) => !memory.content.trim())) throw new Error("Atomic memory content cannot be blank.");
  if (changed.some((memory) => memory.content.trim().length > 4000)) throw new Error("Atomic memory content must be at most 4,000 characters.");
  return { version: draft.version, title: draft.title.trim(), summary: draft.summary.trim(),
    ...(changed.length ? { memories: changed.map((memory) => ({ id: memory.id, version: memory.version, content: memory.content.trim() })) } : {}) };
}
