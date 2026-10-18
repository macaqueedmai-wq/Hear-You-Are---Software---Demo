import type { Card, SummaryGeneration } from "./types";

const summaryStates = {
  "no-sources": { label: "Waiting for sources", message: "No eligible source memories are available for this Uploading date yet." },
  queued: { label: "Summary queued", message: "Your daily summary is queued and will start automatically." },
  running: { label: "Generating summary", message: "Your daily summary is being generated from the current atomic memories." },
  failed: { label: "Summary failed", message: "Daily summary generation failed. You can retry it." },
  unconfigured: { label: "Summary setup required", message: "Daily summary generation is not configured. Check the text-model settings." },
  ready: { label: "Summary ready", message: "Your daily summaries are ready." },
};

export function summaryGenerationDisplay(generation?: SummaryGeneration) {
  const defaults = generation ? summaryStates[generation.status] : undefined;
  return {
    label: defaults?.label || "Summary status unavailable",
    message: generation?.message?.trim() || defaults?.message || "Summary generation status is unavailable. Refresh to check again.",
    polling: generation?.status === "queued" || generation?.status === "running",
    canRetry: generation?.status === "failed",
  };
}

export function dailySummaryContent(card: Pick<Card, "summary" | "analysis_mode">, generation?: SummaryGeneration) {
  if (card.analysis_mode !== "pending") return {
    summary: card.summary,
    tag: card.analysis_mode === "llm" ? "AI summary" : "Summary",
  };
  if (generation?.status === "ready") return {
    summary: "This summary is not available for the current sources. Refresh to check its status.",
    tag: "Summary unavailable",
  };
  const status = summaryGenerationDisplay(generation);
  return { summary: status.message, tag: status.label };
}
