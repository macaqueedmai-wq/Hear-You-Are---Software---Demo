import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { atomicTopicSections, buildRecordingTranscriptView, createTopicDraft, rebaseTopicDraft, topicDraftChanged, topicPatch, uploadingDateLabel } from "../src/topics.ts";
import type { Memory, Recording, Topic } from "../src/types.ts";

const topics: Topic[] = [
  { id: "one", number: 1, version: 3, edited: true, title: "Edited first topic", summary: "A topic summary.", source_segment_ids: ["a", "b"], memory_ids: ["ma", "mb"] },
  { id: "two", number: 2, version: 1, edited: false, title: "Second topic", summary: "Another summary.", source_segment_ids: ["c"], memory_ids: ["mc"] },
];
const recording: Recording = { id: "r", filename: "synthetic.wav", status: "ready", is_demo: false,
  created_at: "2026-10-18T16:30:00Z", recorded_at: "2000-01-01T00:00:00+08:00", topics,
  memory_pipeline: { status: "stale" },
  segments: ["a", "b", "c"].map((id, index) => ({ id, start: index, end: index + 1, text: `Original ${id}.`, subject_id: "unknown", confirmed: false, speaker_label: "speaker_0" })),
};
const memories = ["ma", "mb", "mc"].map((id, index) => ({ id, recording_id: "r", version: index + 2,
  content: `Atomic ${id}.`, status: "pending", subject_id: "unknown", confirmed: false } as Memory));

test("Uploading date comes only from server created_at and is displayed in Hong Kong", () => {
  const label = uploadingDateLabel(recording);
  assert.match(label, /Uploading date/);
  assert.match(label, /19 Oct 2026/);
  assert.match(label, /00:30/);
  assert.doesNotMatch(label, /2000/);
  assert.equal(uploadingDateLabel({}), "Uploading date unavailable");
});
for (const status of ["stale", "failed", "processing"] as const) test(`durable topics remain numbered and editable with a ${status} advisory pipeline`, () => {
  const result = buildRecordingTranscriptView({ ...recording, memory_pipeline: { status } });
  assert.equal(result.kind, "topics");
  assert.deepEqual(result.groups.map((group) => group.number), [1, 2]);
  assert.equal(result.groups[0].topic, topics[0]);
  assert.equal(result.groups[0].title, "Edited first topic");
  assert.deepEqual(result.groups.flatMap((group) => group.segments), recording.segments);
});
test("identity changes do not remove or rewrite durable topics", () => {
  const changed = { ...recording, segments: recording.segments?.map((segment) => ({ ...segment, subject_id: "user", confirmed: true })) };
  assert.equal(buildRecordingTranscriptView(changed).kind, "topics");
  assert.equal(buildRecordingTranscriptView(changed).groups[0].topic, topics[0]);
});
test("invalid canonical partition and legacy transient groups fall back honestly to raw sources", () => {
  const invalid = buildRecordingTranscriptView({ ...recording, topics: [{ ...topics[0], source_segment_ids: ["a", "missing"] }] });
  assert.equal(invalid.kind, "source");
  assert.deepEqual(invalid.groups.flatMap((group) => group.segments), recording.segments);
  const legacy = buildRecordingTranscriptView({ ...recording, topics: undefined, memory_pipeline: { status: "completed", transcript_groups: topics } });
  assert.equal(legacy.kind, "source");
});
test("Atomic memories are mapped by canonical membership and filtered without changing topic identity", () => {
  const sections = atomicTopicSections([recording], [memories[1]]);
  assert.equal(sections.length, 1);
  assert.equal(sections[0].topic, topics[0]);
  assert.deepEqual(sections[0].memories, [memories[1]]);
  const editor = createTopicDraft(recording, sections[0].topic!, memories);
  assert.deepEqual(editor.memories.map((memory) => memory.id), ["ma", "mb"]);
});
test("topic editing is available before identity and sends only changed atomic contents with CAS versions", () => {
  const draft = createTopicDraft(recording, topics[0], memories);
  draft.title = "New title";
  draft.memories[0].content = "Corrected atomic content.";
  assert.deepEqual(topicPatch(draft), { version: 3, title: "New title", summary: "A topic summary.",
    memories: [{ id: "ma", version: 2, content: "Corrected atomic content." }] });
  assert.equal(memories[0].subject_id, "unknown");
  assert.equal(memories[0].confirmed, false);
  assert.equal("confirmed" in topicPatch(draft), false);
  assert.equal("reviewed" in topicPatch(draft), false);
});
test("unchanged atomic contents are omitted from metadata-only edits", () => {
  const draft = createTopicDraft(recording, topics[0], memories);
  assert.equal(topicDraftChanged(draft), false);
  draft.summary = "Changed summary.";
  assert.equal(topicDraftChanged(draft), true);
  assert.equal("memories" in topicPatch(draft), false);
});
test("conflict refresh preserves all entered text while refreshing versions and saved values", () => {
  const draft = createTopicDraft(recording, topics[0], memories);
  draft.title = "My draft title";
  draft.summary = "My draft summary.";
  draft.memories[0].content = "My draft fact.";
  const latestTopic = { ...topics[0], version: 4, title: "Concurrent title" };
  const latestMemories = memories.map((memory) => memory.id === "ma" ? { ...memory, version: 7, content: "Concurrent fact." } : memory);
  const rebased = rebaseTopicDraft(draft, recording, latestTopic, latestMemories);
  assert.equal(rebased.title, "My draft title");
  assert.equal(rebased.summary, "My draft summary.");
  assert.equal(rebased.savedTitle, "Concurrent title");
  assert.equal(rebased.version, 4);
  assert.equal(rebased.memories[0].content, "My draft fact.");
  assert.equal(rebased.memories[0].savedContent, "Concurrent fact.");
  assert.equal(topicPatch(rebased).memories?.[0].version, 7);
});
test("removed atomic memories retain their drafts and block an ambiguous save", () => {
  const draft = createTopicDraft(recording, topics[0], memories);
  draft.memories[1].content = "Preserve this text.";
  const rebased = rebaseTopicDraft(draft, recording, { ...topics[0], memory_ids: ["ma"] }, memories);
  assert.equal(rebased.memories[1].content, "Preserve this text.");
  assert.equal(rebased.memories[1].unavailable, true);
  assert.throws(() => topicPatch(rebased), /no longer available/);
});
test("blank or oversized topic and atomic fields are rejected before sending", () => {
  const draft = createTopicDraft(recording, topics[0], memories);
  assert.throws(() => topicPatch({ ...draft, title: " " }));
  assert.throws(() => topicPatch({ ...draft, summary: "a".repeat(2001) }));
  draft.memories[0].content = "a".repeat(4001);
  assert.throws(() => topicPatch(draft), /4,000/);
});
test("the active UI has no manual date editor, approval action or mandatory processing button", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  for (const retired of ["That's right", "Proactively checking understanding", "Set recording date", "Edit recording date", "expected_recorded_at", "Process with DeepSeek"])
    assert.equal(app.includes(retired), false, retired);
  assert.match(app, /Uploading date/);
  assert.match(app, /Edit topic/);
});
