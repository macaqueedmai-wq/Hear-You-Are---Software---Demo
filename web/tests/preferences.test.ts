import { test } from "node:test";
import assert from "node:assert/strict";
import { supportPreferenceEnabled, supportPreferencePatch } from "../src/proactive.ts";
import type { Settings } from "../src/types.ts";

const settings: Settings = {
  preferences: { reminder: true, suggestion: false, comfort: true, warning: false },
  warning_demo_enabled: false, quiet_mode: true, frequency_minutes: 45, retention_days: 30,
};

test("the real Warning preference is independent of the legacy demo flag", () => {
  for (const preference of [false, true]) for (const demo of [false, true]) {
    const saved = { ...settings, preferences: { ...settings.preferences, warning: preference }, warning_demo_enabled: demo };
    assert.equal(supportPreferenceEnabled(saved, "warning"), preference);
  }
});

test("a Warning action changes only the real preference and preserves the legacy demo flag", () => {
  for (const demo of [false, true]) for (const enabled of [true, false]) {
    const saved = { ...settings, warning_demo_enabled: demo };
    const patch = supportPreferencePatch(saved, "warning", enabled);
    assert.deepEqual(patch, { preferences: { ...settings.preferences, warning: enabled } });
    const next = { ...saved, ...patch };
    assert.equal(next.quiet_mode, true);
    assert.equal(next.frequency_minutes, 45);
    assert.equal(next.retention_days, 30);
    assert.equal(next.warning_demo_enabled, demo);
    assert.equal(supportPreferenceEnabled(next, "warning"), enabled);
  }
  assert.equal(settings.preferences.warning, false);
  assert.equal(settings.warning_demo_enabled, false);
});

test("the other three switches preserve both Warning values, including mismatched legacy flags", () => {
  for (const warning of [false, true]) for (const demo of [false, true]) for (const type of ["reminder", "suggestion", "comfort"] as const) {
    const saved = { ...settings, preferences: { ...settings.preferences, warning }, warning_demo_enabled: demo };
    const enabled = !saved.preferences[type];
    const patch = supportPreferencePatch(saved, type, enabled);
    const next = { ...saved, ...patch };
    assert.deepEqual(next.preferences, { ...saved.preferences, [type]: enabled });
    assert.equal(next.warning_demo_enabled, demo);
    assert.equal("warning_demo_enabled" in patch, false);
  }
});
