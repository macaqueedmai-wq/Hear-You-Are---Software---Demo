import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dailySummaryContent, summaryGenerationDisplay } from "../src/summaries.ts";
import type { SummaryGeneration } from "../src/types.ts";

const pending = { analysis_mode: "pending", summary: "DeepSeek summary pending; do not display this placeholder as a summary." };

for (const status of ["queued", "running", "failed", "unconfigured", "no-sources"] as const) test(`pending cards show the real ${status} state instead of placeholder prose`, () => {
  const generation = { status, message: `Backend ${status} message.` };
  const content = dailySummaryContent(pending, generation);
  assert.equal(content.summary, generation.message);
  assert.doesNotMatch(content.summary, /DeepSeek summary pending/);
  assert.notEqual(content.tag, "AI summary");
});

test("only queued and running jobs request faster automatic polling", () => {
  for (const status of ["no-sources", "queued", "running", "failed", "unconfigured", "ready"] as const) {
    assert.equal(summaryGenerationDisplay({ status, message: "" }).polling, status === "queued" || status === "running");
  }
  assert.equal(summaryGenerationDisplay().polling, false);
});

test("explicit retry is offered only after a true failed job", () => {
  for (const status of ["no-sources", "queued", "running", "failed", "unconfigured", "ready"] as const) {
    assert.equal(summaryGenerationDisplay({ status, message: "" }).canRetry, status === "failed");
  }
  assert.equal(summaryGenerationDisplay().canRetry, false);
});

test("missing job and ready-versus-pending mismatches never claim preparation or completion", () => {
  const unavailable = dailySummaryContent(pending);
  assert.match(unavailable.summary, /status is unavailable/);
  assert.doesNotMatch(unavailable.summary, /being prepared|being generated/);
  const mismatch = dailySummaryContent(pending, { status: "ready", message: "Ready." });
  assert.match(mismatch.summary, /not available for the current sources/);
  assert.equal(mismatch.tag, "Summary unavailable");
});

test("finished model and local summaries retain their content across other job states", () => {
  const generation: SummaryGeneration = { status: "running", message: "Generating." };
  assert.deepEqual(dailySummaryContent({ analysis_mode: "llm", summary: "Actual generated summary." }, generation), { summary: "Actual generated summary.", tag: "AI summary" });
  assert.deepEqual(dailySummaryContent({ analysis_mode: "local_rules", summary: "Actual local summary." }), { summary: "Actual local summary.", tag: "Summary" });
});

test("UI has no unconditional preparation promise or happy-path Generate button", () => {
  const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /Summary is being prepared automatically|Generate AI summary/);
  assert.match(source, /summaryStatus\.canRetry && <button/);
  assert.match(source, /generation=\{state\?\.summary_generation\}/);
});
