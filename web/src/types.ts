export type Category = "mood" | "social" | "moments";
export type CareType = "reminder" | "suggestion" | "comfort" | "warning";
export interface Segment {
  id?: string;
  segment_id?: string;
  start: number;
  end: number;
  text: string;
  speaker?: string;
  subject_id?: string;
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
    | "current_request";
  historical_memory_ids?: string[];
  current_memory_ids?: string[];
  can_schedule_bedtime?: boolean;
}
export interface Settings {
  preferences: Record<CareType, boolean>;
  quiet_mode: boolean;
  frequency_minutes: number;
  warning_demo_enabled: boolean;
  retention_days: number;
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
  assessment: { reason: string; matched_types: CareType[] };
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
}
