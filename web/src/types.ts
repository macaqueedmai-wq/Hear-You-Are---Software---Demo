export type Category = "mood" | "social" | "moments";
export type CareType = "reminder" | "suggestion" | "comfort" | "warning";
export interface Segment {
  id?: string;
  segment_id?: string;
  start: number;
  end: number;
  text: string;
  speaker?: string;
  speaker_label?: string | number | null;
  subject_id?: string;
  confirmed?: boolean;
}
export interface TranscriptGroup {
  id: string;
  title: string;
  summary: string;
  source_segment_ids: string[];
}
export interface Topic extends TranscriptGroup {
  version: number;
  edited: boolean;
  memory_ids: string[];
  number: number;
}
export interface MemoryPipelineStage {
  stage?: string;
  name?: string;
  status?: string;
  error?: string | null;
}
export interface MemoryPipeline {
  status: "pending" | "processing" | "completed" | "failed" | "stale";
  stage?: string;
  error?: string | null;
  model?: string;
  stages?: (string | MemoryPipelineStage)[];
  transcript_groups?: TranscriptGroup[];
  consolidation?: { proposals?: { kind?: string; memory_ids?: string[]; reason: string }[] };
  governance?: { reviews?: { memory_ids?: string[]; reason: string }[] };
}
export interface Recording {
  id: string;
  filename: string;
  status: string;
  error?: string | null;
  recorded_at?: string | null;
  created_at?: string;
  is_demo: boolean;
  segments?: Segment[];
  metrics?: Record<string, unknown>;
  subject_confirmed?: boolean;
  memory_pipeline?: MemoryPipeline | null;
  topics?: Topic[];
  topic_pipeline?: { status: string; stage?: string; error?: string | null };
}
export interface Memory {
  id: string;
  recording_id: string;
  segment_id: string;
  content: string;
  subject_id: string;
  memory_type: string;
  evidence_kind: string;
  scope: string;
  event_time: string | null;
  source_refs: Segment[];
  category: Category;
  status: string;
  version: number;
  confirmed: boolean;
  reviewed?: boolean;
  is_demo?: boolean;
  follow_up?: { due_at: string; allowed: boolean; completed: boolean } | null;
}
export interface WarningTrigger {
  basis: "acute_distress" | "repeated_distress" | "fictional_warning";
  topics: string[];
  dates: string[];
  day_count: number;
  window_days: 1 | 7;
  evidence_quotes: { memory_id: string; text: string }[];
}
export interface Card {
  id: string;
  category: Category;
  title: string;
  summary: string;
  date: string;
  memory_ids: string[];
  source_refs: Segment[];
  versions: Record<string, number>;
  recording_count: number;
  status: string;
  is_demo: boolean;
  demo_only?: boolean;
  provisional?: boolean;
  type?: CareType;
  condition_reason?: string;
  action_text?: string;
  analysis_mode?: string;
  review_status?: "pending" | "confirmed";
  review_prompt?: string;
  review_token?: string;
  support_basis?:
    | "sleep_pattern"
    | "social_follow_up"
    | "emotional_follow_up"
    | "current_request"
    | "acute_distress"
    | "repeated_distress"
    | "fictional_warning";
  warning_trigger?: WarningTrigger;
  historical_memory_ids?: string[];
  current_memory_ids?: string[];
  can_schedule_bedtime?: boolean;
  delivery_mode?: "requested" | "automatic";
  requested_at?: string;
  displayed_at?: string | null;
}
export interface Settings {
  preferences: Record<CareType, boolean>;
  quiet_mode: boolean;
  frequency_minutes: number;
  warning_demo_enabled: boolean;
  retention_days: number;
}
export interface CareTypeAssessment {
  enabled: boolean;
  condition_met: boolean;
  matched_memory_count: number;
  reason_code: string;
  reason: string;
  support_basis?: Card["support_basis"];
  warning_trigger?: WarningTrigger;
  delivery?: {
    status: "disabled" | "not_matched" | "paused" | "eligible" | "shown";
    reason: string;
  };
  request_allowed?: boolean;
  display_time?: SupportDisplayTime;
}
export interface SupportDisplayTime {
  scheduled_at: string | null;
  effective_at: string | null;
  displayed_at: string | null;
  reason: string;
}
export interface ProactiveAssessment {
  reason: string;
  matched_types: CareType[];
  types?: Partial<Record<CareType, CareTypeAssessment>>;
}
export interface SummaryGeneration {
  status: "no-sources" | "queued" | "running" | "failed" | "unconfigured" | "ready";
  message: string;
}
export interface CareGeneration {
  status: "idle" | "pending" | "ready" | "failed" | "unconfigured";
  message: string;
}
export interface CareResponse {
  cards: Card[];
  assessment: ProactiveAssessment;
  care_generation?: CareGeneration;
}
export interface State {
  date: string;
  dates: string[];
  recordings: Recording[];
  memories: Memory[];
  cards: Card[];
  proactive: Card[];
  settings: Settings;
  coverage: Record<string, unknown>;
  assessment: ProactiveAssessment;
  summary_generation?: SummaryGeneration;
  care_generation?: CareGeneration;
  extraction_mode?: string;
}
export interface Source {
  memory: Memory;
  recording: Recording;
  segments: Segment[];
  versions: (Memory & { created_at?: string })[];
}
export interface Health {
  status?: string;
  version?: string;
  deployment?: {
    mode?: "local" | "server";
    audio_processing?: "this_device" | "server" | "external_service";
    shared_workspace?: boolean;
  };
  extraction_mode?: string;
  llm?: Record<string, unknown>;
  asr?: Record<string, unknown>;
  model?: string;
  device?: string;
  analysis?: {
    mode?: string;
    label?: string;
    configured?: boolean;
    external?: boolean;
    model?: string;
  };
  memory_pipeline?: {
    configured?: boolean;
    mode?: "deepseek" | "local_rules";
    provider?: string;
    model?: string;
    stages?: Record<string, { configured: boolean; optional: boolean; model?: string | null }>;
  };
}
