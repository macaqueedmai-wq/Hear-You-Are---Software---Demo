import { test } from "node:test";
import assert from "node:assert/strict";
import { buildTranscriptView, pendingSegmentIds, sanitizeSelection, toggleIds } from "../src/transcript.ts";
import type { MemoryPipeline, Segment } from "../src/types.ts";

const segments: Segment[] = [
  { id: "a", start: 0, end: 2, text: "Original A.", speaker_label: "speaker_0", subject_id: "unknown", confirmed: false },
  { id: "b", start: 2, end: 4, text: "Original B.", speaker_label: "speaker_1", subject_id: "unknown", confirmed: false },
  { id: "c", start: 4, end: 6, text: "Original C.", speaker_label: "speaker_0", subject_id: "user", confirmed: true },
];
const groups = [
  { id: "one", title: "First topic", summary: "AI summary.", source_segment_ids: ["a", "b"] },
  { id: "two", title: "Second topic", summary: "Another AI summary.", source_segment_ids: ["c"] },
];
const completed = (value: unknown): MemoryPipeline => ({ status: "completed", transcript_groups: value as MemoryPipeline["transcript_groups"] });

test("complete ordered topics preserve exact raw fragments and identify mixed speakers", () => {
  const result = buildTranscriptView(segments, completed(groups));
  assert.equal(result.kind, "topics");
  assert.equal(result.groups[0].segments[0], segments[0]);
  assert.deepEqual(result.groups.flatMap((group) => group.segments.map((segment) => segment.text)), segments.map((segment) => segment.text));
  assert.equal(result.groups[0].mixedSpeakers, true);
  assert.deepEqual(result.groups[0].speakerLabels, ["speaker_0", "speaker_1"]);
});

for (const [name, invalid] of [
  ["missing source", [groups[0]]],
  ["unknown source", [{ ...groups[0], source_segment_ids: ["a", "missing"] }, groups[1]]],
  ["duplicate source", [{ ...groups[0], source_segment_ids: ["a", "a", "b"] }, groups[1]]],
  ["out of order", [{ ...groups[0], source_segment_ids: ["b", "a"] }, groups[1]]],
  ["noncontiguous", [{ ...groups[0], source_segment_ids: ["a", "c"] }, { ...groups[1], source_segment_ids: ["b"] }]],
  ["duplicate group id", [groups[0], { ...groups[1], id: "one" }]],
  ["empty group", [{ ...groups[0], source_segment_ids: [] }, groups[1]]],
  ["blank title", [{ ...groups[0], title: " " }, groups[1]]],
  ["invalid summary", [{ ...groups[0], summary: null }, groups[1]]],
  ["malformed payload", { groups }],
] as const) test(`invalid partition falls back without losing sources: ${name}`, () => {
  const result = buildTranscriptView(segments, completed(invalid));
  assert.equal(result.kind, "source");
  assert.deepEqual(result.groups.flatMap((group) => group.segments), segments);
  assert.ok(result.groups.every((group) => group.summary === undefined));
});

for (const status of ["pending", "processing", "failed", "stale"] as const) test(`${status} never presents old groups as AI output`, () => {
  const result = buildTranscriptView(segments, { status, transcript_groups: groups });
  assert.equal(result.kind, "source");
  assert.deepEqual(result.groups.flatMap((group) => group.segments), segments);
});

test("legacy and unidentified fragments remain visible with source labels", () => {
  const legacy = [{ ...segments[0], id: undefined, speaker_label: "unknown" }, segments[1]];
  const result = buildTranscriptView(legacy);
  assert.equal(result.kind, "source");
  assert.equal(result.groups[0].hasUnknownSpeaker, true);
  assert.deepEqual(result.groups.flatMap((group) => group.segments), legacy);
  assert.deepEqual(pendingSegmentIds(legacy), ["b"]);
});

test("semantic groups reject ambiguous source ids and nonchronological originals", () => {
  assert.equal(buildTranscriptView([segments[0], { ...segments[1], id: "a" }], completed(groups)).kind, "source");
  assert.equal(buildTranscriptView([segments[1], segments[0], segments[2]], completed(groups)).kind, "source");
});

test("selection excludes confirmed identities, deduplicates and removes stale ids", () => {
  const allowed = pendingSegmentIds([...segments, segments[0], { ...segments[1], id: "other", confirmed: true }]);
  assert.deepEqual(allowed, ["a", "b"]);
  assert.deepEqual(sanitizeSelection(["b", "a", "a", "c", "removed"], allowed), ["a", "b"]);
  assert.deepEqual(toggleIds([], ["a", "a", "c", "removed"], true, allowed), ["a"]);
  assert.deepEqual(toggleIds(["a", "b"], ["a"], false, allowed), ["b"]);
  assert.deepEqual(toggleIds(["a"], allowed, true, allowed), allowed);
  assert.deepEqual(toggleIds(allowed, allowed, false, allowed), []);
});

test("individual overrides survive topic and global selection without attribution", () => {
  const allowed = pendingSegmentIds(segments);
  let selection = toggleIds([], groups[0].source_segment_ids, true, allowed);
  selection = toggleIds(selection, ["b"], false, allowed);
  assert.deepEqual(selection, ["a"]);
  assert.equal(segments[0].confirmed, false);
  assert.equal(segments[0].subject_id, "unknown");
});
