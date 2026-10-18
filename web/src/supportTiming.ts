import type { CareType, SupportDisplayTime } from "./types";

function timestamp(value?: string | null) {
  if (!value || !/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
  const result = new Date(value);
  return Number.isNaN(result.getTime()) ? null : result;
}

export function formatSupportTime(value?: string | null) {
  const parsed = timestamp(value);
  if (!parsed) return null;
  return `${new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Hong_Kong", day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(parsed)} · Hong Kong (UTC+8)`;
}

export function hongKongInputValue(value?: string | null) {
  const parsed = timestamp(value);
  return parsed ? new Date(parsed.getTime() + 8 * 3600000).toISOString().slice(0, 16) : "";
}

export function parseHongKongDisplayTime(value: string, now = Date.now()) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw new Error("Enter a date and time in Hong Kong (UTC+8).");
  const iso = `${value}:00+08:00`;
  const parsed = timestamp(iso);
  if (!parsed || hongKongInputValue(iso) !== value) throw new Error("Enter a valid date and time in Hong Kong (UTC+8).");
  if (parsed.getTime() <= now) throw new Error("Choose a future display time in Hong Kong (UTC+8).");
  return iso;
}

export interface DisplayTimeDraft {
  type: CareType;
  expected: string | null;
  automatic: boolean;
  value: string;
}

export function createDisplayTimeDraft(type: CareType, timing: SupportDisplayTime): DisplayTimeDraft {
  return { type, expected: timing.scheduled_at, automatic: timing.scheduled_at === null, value: hongKongInputValue(timing.scheduled_at) };
}

export function rebaseDisplayTimeDraft(draft: DisplayTimeDraft, timing: SupportDisplayTime): DisplayTimeDraft {
  return { ...draft, expected: timing.scheduled_at };
}

export function displayTimePatch(draft: DisplayTimeDraft, now = Date.now()) {
  return {
    type: draft.type,
    display_at: draft.automatic ? null : parseHongKongDisplayTime(draft.value, now),
    expected_display_at: draft.expected,
  };
}

export function supportTimeLabels(timing?: SupportDisplayTime) {
  return {
    scheduled: !timing ? "Unavailable" : timing.scheduled_at === null ? "Automatic timing" : formatSupportTime(timing.scheduled_at) || "Unavailable",
    next: formatSupportTime(timing?.effective_at) || "No confirmed display time",
    displayed: !timing ? "Unavailable" : timing.displayed_at === null ? "Not displayed yet" : formatSupportTime(timing.displayed_at) || "Unavailable",
    reason: timing?.reason || "Display-time information is unavailable.",
  };
}
