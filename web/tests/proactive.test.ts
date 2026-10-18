import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { careConditionDisplay, careEmptyState, revealRequestedSupport } from "../src/proactive.ts";
import type { CareTypeAssessment, ProactiveAssessment } from "../src/types.ts";

const detail = (overrides: Partial<CareTypeAssessment> = {}): CareTypeAssessment => ({
  enabled: true, condition_met: false, matched_memory_count: 0,
  reason_code: "no_explicit_actionable_request", reason: "No explicit request for practical help was found.",
  ...overrides,
});
const assessment = (overrides: Partial<ProactiveAssessment> = {}): ProactiveAssessment => ({
  reason: "No conditions are met; nothing is shown.", matched_types: [], ...overrides,
});

test("each support tile uses its own backend reason, not the overall assessment", () => {
  const checked = assessment({ reason: "Quiet mode is on.", types: {
    reminder: detail({ reason_code: "no_allowed_follow_up", reason: "No allowed follow-up is scheduled." }),
    suggestion: detail(),
    comfort: detail({ condition_met: true, matched_memory_count: 1, reason_code: "conditions_met", reason: "A confirmed source explicitly requests reassurance." }),
  }});
  assert.equal(careConditionDisplay("reminder", checked).reason, "No allowed follow-up is scheduled.");
  assert.equal(careConditionDisplay("suggestion", checked).reason, "No explicit request for practical help was found.");
  assert.equal(careConditionDisplay("comfort", checked).reason, "A confirmed source explicitly requests reassurance.");
  assert.equal(careConditionDisplay("comfort", checked).badge, "Source conditions met");
  assert.equal(checked.reason, "Quiet mode is on.");
});

test("per-type condition result is authoritative over aggregate matches", () => {
  const checked = assessment({ matched_types: ["suggestion"], types: { suggestion: detail() }});
  assert.equal(careConditionDisplay("suggestion", checked).matched, false);
  assert.equal(careConditionDisplay("suggestion", checked).badge, "Not triggered");
});

test("disabled support never receives a matched badge", () => {
  const checked = assessment({ matched_types: ["comfort"], types: { comfort: detail({ enabled: false, condition_met: true, reason: "Comfort is turned off." }) }});
  assert.equal(careConditionDisplay("comfort", checked).badge, "Turned off");
  assert.equal(careConditionDisplay("comfort", checked).matched, false);
  assert.equal(careConditionDisplay("comfort", checked).reason, "Comfort is turned off.");
  assert.equal(careConditionDisplay("suggestion", assessment(), { suggestion: false }).badge, "Turned off");
});

test("legacy or missing assessments stay honest without fabricating a diagnostic", () => {
  const legacy = careConditionDisplay("reminder", assessment({ matched_types: ["reminder"] }));
  assert.equal(legacy.badge, "Source conditions met");
  assert.match(legacy.reason, /per-type reason is not available/);
  const missing = careConditionDisplay("comfort");
  assert.equal(missing.badge, "Not triggered");
  assert.match(missing.reason, /Refresh conditions/);
});

test("support requirements name explicit requests and preserve the sleep-pattern alternative", () => {
  const suggestion = careConditionDisplay("suggestion").requirement;
  assert.match(suggestion, /explicit request for practical help/);
  assert.match(suggestion, /repeated poor-sleep pattern/);
  assert.match(careConditionDisplay("reminder").requirement, /allowed follow-up.*due time/);
  assert.match(careConditionDisplay("comfort").requirement, /request for reassurance or support/);
  const warning = careConditionDisplay("warning").requirement;
  assert.match(warning, /confirmed words describe strong distress today/);
  assert.match(warning, /same kind.*at least 3 days.*past 7 days, including today/);
  assert.match(warning, /supportive check-in, not a diagnosis/);
  assert.doesNotMatch(warning, /fictional|demo switches/);
});

test("a source match with snooze or cooldown shows the actual delivery blocker", () => {
  for (const reason of ["Support is snoozed until 20:50 Hong Kong time.", "The minimum interval has not passed."]) {
    const checked = assessment({ matched_types: ["comfort"], types: { comfort: detail({
      condition_met: true, reason_code: "conditions_met", reason: "Confirmed sources request comfort.",
      delivery: { status: "paused", reason }, request_allowed: true,
    }) }});
    const display = careConditionDisplay("comfort", checked);
    assert.equal(display.matched, true);
    assert.equal(display.badge, "Delivery paused");
    assert.equal(display.deliveryReason, reason);
    assert.equal(display.canRequestSupport, true);
  }
});

test("all four Display Now actions require backend permission, matching sources and enabled preferences", () => {
  for (const type of ["reminder", "suggestion", "comfort", "warning"] as const) {
    const checked = (overrides: Partial<CareTypeAssessment>) => assessment({ types: { [type]: detail({ condition_met: true, request_allowed: true, ...overrides }) } });
    assert.equal(careConditionDisplay(type, checked({})).canRequestSupport, true);
    assert.equal(careConditionDisplay(type, checked({ enabled: false })).canRequestSupport, false);
    assert.equal(careConditionDisplay(type, checked({ condition_met: false })).canRequestSupport, false);
    assert.equal(careConditionDisplay(type, checked({ request_allowed: false })).canRequestSupport, false);
    assert.equal(careConditionDisplay(type, checked({ request_allowed: undefined })).canRequestSupport, false);
    assert.equal(careConditionDisplay(type, checked({}), { [type]: false }).canRequestSupport, false);
  }
});

test("requested support UI uses a dedicated action without patching global settings", () => {
  const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  const action = source.slice(source.indexOf("async function requestSupport("), source.indexOf("function openDisplayTimeEditor("));
  assert.match(action, /api<CareResponse>\("\/proactive\/display-now"/);
  assert.match(action, /JSON\.stringify\(\{ type, date: selectedDate \}\)/);
  assert.match(action, /proactive: result\.cards/);
  assert.match(action, /await refresh\(date, true\)/);
  assert.doesNotMatch(action, /\/settings|quiet_mode:|snoozed_until:|frequency_minutes:/);
  assert.match(source, /!condition\.canRequestSupport/);
  assert.match(source, /Display Proactive Support Now/);
  assert.doesNotMatch(source, /Comfort me now|\/comfort-now/);
  assert.match(source, /state\?\.care_generation\?\.status === "pending"/);
});

test("an in-flight support request never masquerades as a quiet empty state", () => {
  assert.equal(careEmptyState(undefined, true).title, "Preparing your support");
  assert.equal(careEmptyState({ status: "pending", message: "Generating requested comfort." }).message, "Generating requested comfort.");
  assert.equal(careEmptyState({ status: "failed", message: "Provider failed." }).message, "Provider failed.");
  assert.equal(careEmptyState({ status: "unconfigured", message: "Text-model setup is required." }).title, "Support is not available yet");
  assert.equal(careEmptyState({ status: "idle", message: "Snoozed." }).title, "No support card is shown");
});

test("successful explicit support focuses and reveals the actual card once", () => {
  const calls: unknown[] = [];
  assert.equal(revealRequestedSupport("requested-1", (id) => {
    assert.equal(id, "care-card-requested-1");
    return {
      scrollIntoView: (options) => calls.push(["scroll", options]),
      focus: (options) => calls.push(["focus", options]),
    };
  }), true);
  assert.deepEqual(calls, [["scroll", { behavior: "smooth", block: "start" }], ["focus", { preventScroll: true }]]);
  assert.equal(revealRequestedSupport("missing", () => null), false);
});

test("background refreshes cannot initiate result scrolling", () => {
  const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.equal((source.match(/setSupportResultToFocus\(requestedCard\.id\)/g) || []).length, 1);
  const action = source.slice(source.indexOf("async function requestSupport("), source.indexOf("function openDisplayTimeEditor("));
  assert.match(action, /if \(requestedCard\) setSupportResultToFocus\(requestedCard\.id\)/);
  assert.match(source, /if \(!supportResultToFocus\) return/);
  assert.match(source, /setSupportResultToFocus\(null\)/);
});
