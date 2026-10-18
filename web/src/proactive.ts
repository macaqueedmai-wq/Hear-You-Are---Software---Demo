import type { CareGeneration, CareType, ProactiveAssessment, Settings } from "./types";

export function supportPreferenceEnabled(settings: Settings, type: CareType) {
  return settings.preferences[type] === true;
}

export function supportPreferencePatch(settings: Settings, type: CareType, enabled: boolean): Partial<Settings> {
  return {
    preferences: { ...settings.preferences, [type]: enabled },
  };
}

export const careRequirements: Record<CareType, string> = {
  reminder: "An allowed follow-up with a due time that has arrived, and not completed.",
  suggestion: "An explicit request for practical help with a workable next step, or a supported repeated poor-sleep pattern.",
  comfort: "An explicit request for reassurance or support, or a supported emotional or social follow-up.",
  warning: "Your confirmed words describe strong distress today, or the same kind of emotional or social strain on at least 3 days in the past 7 days, including today. A warning offers a supportive check-in, not a diagnosis.",
};
const deliveryLabels = {
  disabled: "Turned off",
  not_matched: "Not triggered",
  paused: "Delivery paused",
  eligible: "Eligible for support",
  shown: "Support available",
};

export function careConditionDisplay(
  type: CareType,
  assessment?: ProactiveAssessment,
  preferences?: Partial<Record<CareType, boolean>>,
) {
  const diagnostic = assessment?.types?.[type];
  const enabled = preferences?.[type] !== false && diagnostic?.enabled !== false;
  const matched = enabled && (diagnostic?.condition_met ?? assessment?.matched_types?.includes(type) ?? false);
  const delivery = diagnostic?.delivery;
  return {
    matched,
    badge: !enabled ? "Turned off" : delivery ? deliveryLabels[delivery.status] : matched ? "Source conditions met" : "Not triggered",
    reason: diagnostic?.reason?.trim() || (!enabled
      ? "This support type is turned off."
      : "A per-type reason is not available yet. Refresh conditions to check again."),
    requirement: careRequirements[type],
    deliveryReason: delivery?.reason?.trim() || "",
    canRequestSupport: matched && diagnostic?.request_allowed === true,
  };
}

export function careEmptyState(generation?: CareGeneration, requesting = false) {
  if (requesting || generation?.status === "pending") return {
    title: "Preparing your support",
    message: generation?.status === "pending" ? generation.message : "Your support request is being checked and prepared.",
  };
  if (generation?.status === "failed" || generation?.status === "unconfigured") return {
    title: "Support is not available yet",
    message: generation.message,
  };
  return {
    title: "No support card is shown",
    message: "The delivery checks above explain why. You can request an eligible support type now.",
  };
}

export function revealRequestedSupport(
  cardId: string,
  findElement: (id: string) => Pick<HTMLElement, "scrollIntoView" | "focus"> | null,
) {
  const card = findElement(`care-card-${cardId}`);
  if (!card) return false;
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  card.focus({ preventScroll: true });
  return true;
}
