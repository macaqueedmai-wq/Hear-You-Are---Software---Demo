import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createDisplayTimeDraft, displayTimePatch, formatSupportTime, hongKongInputValue, parseHongKongDisplayTime, rebaseDisplayTimeDraft, supportTimeLabels } from "../src/supportTiming.ts";
import type { SupportDisplayTime } from "../src/types.ts";

const timing: SupportDisplayTime = { scheduled_at: "2026-10-18T16:30:00Z", effective_at: null, displayed_at: null, reason: "Quiet mode makes the next automatic display time unknown." };
const now = Date.parse("2026-10-18T12:00:00Z");

test("display labels and edit inputs always convert known timestamps to Hong Kong", () => {
  assert.match(formatSupportTime(timing.scheduled_at)!, /19 Oct 2026/);
  assert.match(formatSupportTime(timing.scheduled_at)!, /00:30/);
  assert.match(formatSupportTime(timing.scheduled_at)!, /Hong Kong \(UTC\+8\)/);
  assert.equal(hongKongInputValue(timing.scheduled_at), "2026-10-19T00:30");
  assert.equal(hongKongInputValue("2026-10-18T20:30:00+08:00"), "2026-10-18T20:30");
});

test("an entered display time has an explicit Hong Kong offset independent of device timezone", () => {
  assert.equal(parseHongKongDisplayTime("2026-10-18T20:01", now), "2026-10-18T20:01:00+08:00");
  assert.equal(Date.parse(parseHongKongDisplayTime("2026-10-19T00:30", now)), Date.parse(timing.scheduled_at!));
});

test("invalid calendar dates, ambiguous timestamps and past inputs are rejected", () => {
  for (const value of ["", "2026-02-30T20:00", "2026-13-01T20:00", "2026-10-18T24:01", "2026-10-18T20:00Z", "2026-10-18T20:00:00"]) {
    assert.throws(() => parseHongKongDisplayTime(value, now));
  }
  assert.throws(() => parseHongKongDisplayTime("2026-10-18T20:00", now), /future/);
  assert.throws(() => parseHongKongDisplayTime("2026-10-18T19:59", now), /future/);
  assert.equal(formatSupportTime("2026-10-18T20:01:00"), null);
  assert.equal(hongKongInputValue("not-a-time"), "");
});

test("unknown timing never invents a date, midnight, last display or guaranteed ETA", () => {
  const labels = supportTimeLabels(timing);
  assert.equal(labels.next, "No confirmed display time");
  assert.equal(labels.displayed, "Not displayed yet");
  assert.equal(labels.reason, timing.reason);
  assert.equal(supportTimeLabels().scheduled, "Unavailable");
  assert.equal(supportTimeLabels().displayed, "Unavailable");
  assert.equal(hongKongInputValue(null), "");
  const draft = createDisplayTimeDraft("suggestion", { ...timing, scheduled_at: null });
  assert.equal(draft.automatic, true);
  assert.equal(draft.value, "");
});

test("automatic reset sends explicit null with the exact original schedule CAS token", () => {
  const draft = createDisplayTimeDraft("comfort", timing);
  assert.deepEqual(displayTimePatch({ ...draft, automatic: true }, now), {
    type: "comfort", display_at: null, expected_display_at: timing.scheduled_at,
  });
});

test("scheduling all four types sends only their one-off date and original CAS token", () => {
  for (const type of ["reminder", "suggestion", "comfort", "warning"] as const) {
    const patch = displayTimePatch({ ...createDisplayTimeDraft(type, timing), value: "2026-10-18T21:00" }, now);
    assert.deepEqual(patch, { type, display_at: "2026-10-18T21:00:00+08:00", expected_display_at: timing.scheduled_at });
    assert.equal("preferences" in patch, false);
    assert.equal("frequency_minutes" in patch, false);
  }
});

test("schedule conflict refresh keeps entered date and reset choice while updating expected saved time", () => {
  const draft = { ...createDisplayTimeDraft("reminder", timing), value: "2026-10-18T21:15" };
  const newer = { ...timing, scheduled_at: "2026-10-18T22:00:00+08:00" };
  const rebased = rebaseDisplayTimeDraft(draft, newer);
  assert.equal(rebased.value, draft.value);
  assert.equal(rebased.automatic, false);
  assert.equal(rebased.expected, newer.scheduled_at);
  assert.equal(rebaseDisplayTimeDraft({ ...draft, automatic: true }, newer).automatic, true);
});

test("last displayed uses only the backend actual display timestamp", () => {
  const labels = supportTimeLabels({ ...timing, displayed_at: "2026-10-18T12:10:00Z" });
  assert.match(labels.displayed, /20:10/);
  assert.notEqual(labels.displayed, labels.scheduled);
  assert.equal(supportTimeLabels({ ...timing, displayed_at: "invalid" }).displayed, "Unavailable");
});

test("all four tiles expose adjacent generic controls and explicit schedule save without automatic submission", () => {
  const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.match(source, /\["reminder", "suggestion", "comfort", "warning"\] as CareType\[\]/);
  assert.match(source, /Edit display time[\s\S]*Display Proactive Support Now/);
  assert.match(source, /api<State>\("\/proactive\/display-time", \{ method: "PATCH", body: JSON.stringify\(patch\)/);
  assert.match(source, /onSubmit=\{saveDisplayTime\}/);
  assert.match(source, /Refresh saved time; keep my draft/);
});
