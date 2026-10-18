import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  AudioLines,
  Bell,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  Ear,
  FileAudio,
  Heart,
  Layers3,
  LoaderCircle,
  LockKeyhole,
  MessageCircle,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Sprout,
  Trash2,
  UploadCloud,
  Users,
  X,
} from "lucide-react";
import { SidebarItem } from "./components/SidebarItem";
import { DatasetPanel } from "./components/DatasetPanel";
import { api, ApiError, englishMessage } from "./api";
import { buildTranscriptView, getSegmentId, isIdentityConfirmed, pendingSegmentIds, sanitizeSelection, toggleIds } from "./transcript";
import { atomicTopicSections, buildRecordingTranscriptView, createTopicDraft, rebaseTopicDraft, topicPatch, topicDraftChanged, uploadingDateLabel } from "./topics";
import { careConditionDisplay, careEmptyState, revealRequestedSupport, supportPreferenceEnabled, supportPreferencePatch } from "./proactive";
import { createDisplayTimeDraft, displayTimePatch, formatSupportTime, rebaseDisplayTimeDraft, supportTimeLabels } from "./supportTiming";
import type { DisplayTimeDraft } from "./supportTiming";
import { dailySummaryContent, summaryGenerationDisplay } from "./summaries";
import type {
  Card,
  CareResponse,
  CareType,
  Category,
  Health,
  Memory,
  MemoryPipeline,
  Topic,
  Recording,
  Settings,
  Source,
  State,
  SummaryGeneration,
} from "./types";

type View = "daily" | "import" | "memory" | "care";
type Danger = { title: string; description: string; run: () => Promise<void> };
type TopicDraft = ReturnType<typeof createTopicDraft> & { error: string; conflict: boolean; notice?: string };
type DisplayTimeEditor = DisplayTimeDraft & { error: string; conflict: boolean; notice?: string };
const categoryLabels: Record<Category, string> = {
  mood: "Mood & feelings",
  social: "Social & connection",
  moments: "Memorable moments",
};
const careLabels: Record<CareType, string> = {
  reminder: "Reminder",
  suggestion: "Suggestion",
  comfort: "Comfort",
  warning: "Warning",
};
const categoryIcons = { mood: Heart, social: Users, moments: Sprout };
const viewNames: Record<View, string> = {
  daily: "Daily cards",
  import: "Import recording",
  memory: "Atomic memories",
  care: "Proactive AI",
};
const statusNames: Record<string, string> = {
  queued: "Queued",
  processing: "Transcribing",
  transcribing: "Transcribing",
  ready: "Transcribed",
  completed: "Transcribed",
  pending: "Awaiting confirmation",
  failed: "Processing failed",
  error: "Processing failed",
  confirmed: "Confirmed",
  active: "Active",
  deleted: "Deleted",
  superseded: "Updated",
  uploaded: "Imported",
};
const scopeLabels: Record<string, string> = {
  recorded_conversation: "This conversation",
  stated_preference: "Explicitly stated preference",
  current_context: "Current context",
};
function timeLabel(seconds: number) {
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
}
function localDate() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Hong_Kong",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}
function niceDate(date: string) {
  return new Intl.DateTimeFormat("en-GB", {
    month: "long",
    day: "numeric",
    weekday: "long",
    timeZone: "Asia/Hong_Kong",
  }).format(new Date(`${date}T12:00:00+08:00`));
}
function errorText(error: unknown) {
  return error instanceof Error ? error.message : "The action did not complete. Please try again.";
}

function memoryTransferNotice(provider?: string) {
  if (provider === "DeepSeek")
    return "Transcript text and relevant memories are sent directly to the official DeepSeek API.";
  if (provider === "A2Agent")
    return "Transcript text and relevant memories are sent to DeepSeek through A2Agent, a third-party gateway.";
  return "Transcript text and relevant memories are sent to the configured text-model endpoint.";
}

function transcriptionProviderLabel(provider: unknown) {
  if (provider === "sensevoice") return "SenseVoiceSmall INT8";
  if (provider === "qwen") return "Qwen3-ASR";
  if (provider === "deepgram") return "Deepgram";
  if (provider === "tencent") return "Tencent Cloud ASR";
  if (provider === "whisper") return "Local transcription model";
  return "the configured transcription service";
}

function anonymousSpeakerLabel(label?: string | number | null) {
  if (label === undefined || label === null || String(label) === "unknown" || String(label) === "") return "";
  return `Speaker ${String(label).replace(/^speaker_/, "")}`;
}

const transcriptionStageNames: Record<string, string> = {
  loading_asr: "Loading transcription model",
  transcribing: "Transcribing",
  loading_aligner: "Loading alignment model",
  aligning: "Estimating word timing",
  diarizing: "Distinguishing speakers",
};
function PipelineProgress({ pipeline, recording }: { pipeline?: MemoryPipeline | null; recording?: Recording }) {
  if (!pipeline && !recording) return null;
  const transcribing = ["queued", "processing", "transcribing"].includes(recording?.status || "");
  const organizing = ["pending", "processing"].includes(recording?.topic_pipeline?.status || "");
  const working = transcribing || organizing || ["pending", "processing"].includes(pipeline?.status || "");
  const error = recording?.topic_pipeline?.error || pipeline?.error;
  const failed = recording?.topic_pipeline?.status === "failed" || pipeline?.status === "failed";
  return <div className="small-text processing-progress" role="status" aria-live="polite">
    <p><strong>Transcribing → Atomic memories → Topics</strong></p>
    <p>{working && <LoaderCircle size={13} className="spin" />} {transcribing ? "Transcribing automatically" : organizing ? "Organizing topics automatically" : failed ? "Processing did not complete" : working ? pipeline?.stage === "atomic" ? "Building atomic memories" : "Preparing topics and summaries" : recording?.topics?.length ? "Atomic memories and topics ready" : "Automatic processing"}</p>
    {error && <p className="error-text">{englishMessage(error)}</p>}
  </div>;
}

function SelectionCheckbox({ label, ariaLabel, checked, indeterminate = false, disabled, onChange }: {
  label: string; ariaLabel?: string; checked: boolean; indeterminate?: boolean;
  disabled: boolean; onChange: (checked: boolean) => void;
}) {
  const checkbox = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (checkbox.current) checkbox.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <label className="transcript-checkbox">
      <input ref={checkbox} type="checkbox" checked={checked} disabled={disabled}
        aria-label={ariaLabel || label} aria-checked={indeterminate ? "mixed" : checked}
        onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function Modal({
  children,
  title,
  close,
  className = "",
}: {
  children: ReactNode;
  title: string;
  close: () => void;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current
      ?.querySelector<HTMLElement>("button, input, textarea, select")
      ?.focus();
    function key(event: KeyboardEvent) {
      if (event.key === "Escape") close();
      if (event.key !== "Tab") return;
      const nodes = [
        ...(ref.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex="0"]',
        ) ?? []),
      ];
      const first = nodes[0],
        last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = overflow;
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [close]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        ref={ref}
        className={`modal ${className}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-button" onClick={close} aria-label="Close dialog">
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Empty({
  icon,
  title,
  children,
  action,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}

/** Card anatomy adapted from MoodHaven InsightCard.tsx (MIT): heading, message, evidence. */
function DailyCard({ card, generation, open, edit, busy }: { card: Card; generation?: SummaryGeneration; open: () => void; edit: () => void; busy: boolean }) {
  const Icon = categoryIcons[card.category] || Sprout;
  const content = dailySummaryContent(card, generation);
  return <article className={`daily-card category-${card.category}`}>
    <div className="card-topline"><span className="category-icon"><Icon size={22} /></span><span className="eyebrow">{categoryLabels[card.category]}</span></div>
    <h3>{card.title}</h3><p className="card-summary">{content.summary}</p>
    <div className="card-tags"><span className="tag">{content.tag}</span></div>
    <div className="review-actions"><button className="small-button" disabled={busy} onClick={open}>Sources</button><button className="small-button ghost" disabled={busy} onClick={edit}>Edit topic</button></div>
    <div className="card-footer"><span><AudioLines size={14} />{card.memory_ids?.length || 0} atomic {card.memory_ids?.length === 1 ? "memory" : "memories"}</span></div>
  </article>;
}

function CareCard({
  card,
  feedback,
  open,
  memories,
  recordings,
  openMemory,
  schedule,
  busy,
}: {
  card: Card;
  feedback: (id: string, action: string) => void;
  open: () => void;
  memories: Memory[];
  recordings: Recording[];
  openMemory: (id: string) => void;
  schedule: (card: Card, dueAt: string) => Promise<boolean>;
  busy: boolean;
}) {
  const type = card.type || "suggestion";
  const [showSchedule, setShowSchedule] = useState(false);
  const [bedtime, setBedtime] = useState("");
  const [scheduleError, setScheduleError] = useState("");
  const historical = memories.filter((memory) =>
    card.historical_memory_ids?.includes(memory.id),
  );
  const currentIds = card.current_memory_ids || card.memory_ids;
  const current = memories.filter(
    (memory) =>
      currentIds?.includes(memory.id) &&
      !card.historical_memory_ids?.includes(memory.id),
  );
  const supportLabels = {
    sleep_pattern: "Sleep record across days",
    social_follow_up: "Social follow-up",
    emotional_follow_up: "Emotional follow-up",
    current_request: "Current explicit need",
    acute_distress: "Strong distress in your current words",
    repeated_distress: "Emotional or social strain across days",
    fictional_warning: "Fictional warning demonstration",
  };
  async function scheduleBedtime(event: FormEvent) {
    event.preventDefault();
    setScheduleError("");
    if (!bedtime || new Date(`${bedtime}:00+08:00`).getTime() <= Date.now()) {
      setScheduleError("Choose a future Hong Kong time.");
      return;
    }
    if (await schedule(card, `${bedtime}:00+08:00`)) {
      setShowSchedule(false);
      setBedtime("");
    } else
      setScheduleError(
        "The reminder could not be set. Check the message above, review the sources, then try again.",
      );
  }
  return (
    <article id={`care-card-${card.id}`} tabIndex={-1} aria-label={`${careLabels[type]}: ${card.title}`} className={`care-card care-${type}`}>
      <div className="care-card-heading">
        <span className="care-symbol">
          {type === "comfort" ? (
            <Heart size={20} />
          ) : type === "reminder" ? (
            <Bell size={20} />
          ) : (
            <Sparkles size={20} />
          )}
        </span>
        <div>
          <span className="eyebrow">{careLabels[type]}</span>
          <h3>{card.title}</h3>
        </div>
      </div>
      {type === "warning" && card.demo_only === true && <div className="support-label">Fictional demo</div>}
      {card.delivery_mode === "requested" && <div className="support-label"><Heart size={13} />Requested now · Your automatic support schedule is unchanged</div>}
      {formatSupportTime(card.displayed_at) && <p className="small-text muted"><time dateTime={card.displayed_at!}>Displayed · {formatSupportTime(card.displayed_at)}</time></p>}
      <p>{card.summary || card.action_text}</p>
      <div className="condition">
        <ShieldCheck size={16} />
        <span>
          <strong>{type === "warning" ? "Why this warning" : "Why this appeared"}</strong>
          {card.condition_reason}
        </span>
      </div>
      {card.support_basis && (
        <div className="support-label">
          <Layers3 size={13} />
          {supportLabels[card.support_basis]}
        </div>
      )}
      {(historical.length > 0 || current.length > 0) && (
        <div className="support-sources">
          {historical.length > 0 && (
            <div>
              <h4>Earlier records</h4>
              {historical.map((memory) => (
                <button
                  className="support-source"
                  key={memory.id}
                  onClick={() => openMemory(memory.id)}
                >
                  <span>{uploadingDateLabel(recordings.find((recording) => recording.id === memory.recording_id))}</span>
                  <strong>{memory.content}</strong>
                  <ArrowRight size={13} />
                </button>
              ))}
            </div>
          )}
          {current.length > 0 && (
            <div>
              <h4>Current records</h4>
              {current.map((memory) => (
                <button
                  className="support-source"
                  key={memory.id}
                  onClick={() => openMemory(memory.id)}
                >
                  <span>{uploadingDateLabel(recordings.find((recording) => recording.id === memory.recording_id))}</span>
                  <strong>{memory.content}</strong>
                  <ArrowRight size={13} />
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {card.can_schedule_bedtime && (
        <div className="bedtime-section">
          {!showSchedule ? (
            <button
              className="small-button"
              disabled={busy}
              onClick={() => setShowSchedule(true)}
            >
              <Bell size={14} />
              Set a bedtime reminder
            </button>
          ) : (
            <form onSubmit={(event) => void scheduleBedtime(event)}>
              <label>
                When would you like the bedtime reminder? (Hong Kong time)
                <input
                  aria-label="Bedtime reminder time"
                  type="datetime-local"
                  required
                  value={bedtime}
                  onChange={(event) => setBedtime(event.target.value)}
                  min={new Date(Date.now() + 8 * 3600000 + 60000)
                    .toISOString()
                    .slice(0, 16)}
                />
              </label>
              {scheduleError && (
                <p className="error-text" role="alert">
                  {scheduleError}
                </p>
              )}
              <div>
                <button
                  className="small-button"
                  type="submit"
                  disabled={busy || !bedtime}
                >
                  <Check size={14} />
                  Allow and set reminder
                </button>
                <button
                  className="text-button"
                  type="button"
                  onClick={() => setShowSchedule(false)}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
          <p>A reminder is created only when you set it explicitly. It is checked while the app is open; there is no background system alarm.</p>
        </div>
      )}
      <div className="care-actions">
        <button
          className="small-button"
          disabled={busy}
          onClick={() =>
            feedback(card.id, type === "reminder" ? "done" : "helpful")
          }
        >
          <Check size={15} />
          {type === "reminder" ? "Done" : "This helped"}
        </button>
        <button
          className="small-button ghost"
          disabled={busy}
          onClick={() => feedback(card.id, "snooze")}
        >
          <Clock3 size={14} />
          Show later
        </button>
        <button className="text-button" onClick={open}>
          Sources <ArrowRight size={14} />
        </button>
        <button
          className="icon-button"
          aria-label="Dismiss this care card"
          disabled={busy}
          onClick={() => feedback(card.id, "dismiss")}
        >
          <X size={16} />
        </button>
      </div>
    </article>
  );
}

export default function App() {
  const [view, setView] = useState<View>("daily");
  const [date, setDate] = useState("");
  const [state, setState] = useState<State | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [danger, setDanger] = useState<Danger | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [drawerRecording, setDrawerRecording] = useState<Recording | null>(
    null,
  );
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState("");
  const [selectedSegments, setSelectedSegments] = useState<string[]>([]);
  const [topicEditor, setTopicEditor] = useState<TopicDraft | null>(null);
  const topicSaveInFlight = useRef(false);
  const supportRequestInFlight = useRef(false);
  const displayTimeSaveInFlight = useRef(false);
  const [supportResultToFocus, setSupportResultToFocus] = useState<string | null>(null);
  const [displayTimeEditor, setDisplayTimeEditor] = useState<DisplayTimeEditor | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const selectedDate = date || state?.date || localDate();
  const closeDrawer = useCallback(() => {
    setSource(null);
    setDrawerRecording(null);
    setSourceError("");
    setSelectedSegments([]);
  }, []);
  const closeSettings = useCallback(() => setShowSettings(false), []);
  const closeDanger = useCallback(() => setDanger(null), []);
  const closeTopicEditor = useCallback(() => {
    if (!topicSaveInFlight.current) setTopicEditor(null);
  }, []);
  const closeDisplayTimeEditor = useCallback(() => {
    if (!displayTimeSaveInFlight.current) setDisplayTimeEditor(null);
  }, []);

  const refresh = useCallback(
    async (selected = date, silent = false) => {
      if (!silent) setLoading(true);
      try {
        const [next, status] = await Promise.all([
          api<State>(
            `/state${selected ? `?date=${encodeURIComponent(selected)}` : ""}`,
          ),
          api<Health>("/health"),
        ]);
        setState(next);
        setHealth(status);
        setError("");
        return next;
      } catch (err) {
        setError(errorText(err));
      } finally {
        setLoading(false);
      }
    },
    [date],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    const interval = summaryGenerationDisplay(state?.summary_generation).polling || state?.care_generation?.status === "pending" ? 2000 : 5000;
    const timer = window.setInterval(() => void refresh(date, true), interval);
    return () => window.clearInterval(timer);
  }, [refresh, date, state?.summary_generation?.status, state?.care_generation?.status]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 7000);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    document.title = `Hear You Are · ${viewNames[view]}`;
  }, [view]);

  async function run(
    label: string,
    operation: () => Promise<unknown>,
    success?: string,
  ) {
    setBusy(label);
    setError("");
    try {
      await operation();
      await refresh(date, true);
      if (success) setNotice(success);
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy("");
    }
  }
  async function openMemory(id: string) {
    setSourceLoading(true);
    setSourceError("");
    setSource(null);
    setDrawerRecording(null);
    try {
      const next = await api<Source>(`/memories/${id}/source`);
      setSource(next);
      setSelectedSegments([]);
    } catch (err) {
      setSourceError(errorText(err));
    } finally {
      setSourceLoading(false);
    }
  }
  async function requestSupport(type: CareType) {
    if (supportRequestInFlight.current) return;
    supportRequestInFlight.current = true;
    setBusy(`display-now-${type}`);
    setError("");
    try {
      const result = await api<CareResponse>("/proactive/display-now", {
        method: "POST", body: JSON.stringify({ type, date: selectedDate }),
      });
      setState((previous) => previous ? { ...previous, proactive: result.cards, assessment: result.assessment, care_generation: result.care_generation } : previous);
      await refresh(date, true);
      const requestedCard = result.cards.find((card) => card.type === type);
      if (requestedCard) setSupportResultToFocus(requestedCard.id);
      setNotice(requestedCard ? `Your ${careLabels[type].toLowerCase()} card is ready.` : result.care_generation?.message || "Your support request was checked.");
    } catch (err) {
      await refresh(date, true);
      setError(errorText(err));
    } finally {
      supportRequestInFlight.current = false;
      setBusy("");
    }
  }
  function openDisplayTimeEditor(type: CareType) {
    const timing = state?.assessment.types?.[type]?.display_time;
    if (timing) setDisplayTimeEditor({ ...createDisplayTimeDraft(type, timing), error: "", conflict: false });
  }
  async function saveDisplayTime(event: FormEvent) {
    event.preventDefault();
    const draft = displayTimeEditor;
    if (!draft || busy || draft.conflict) return;
    let patch;
    try { patch = displayTimePatch(draft); }
    catch (err) { setDisplayTimeEditor((previous) => previous ? { ...previous, error: errorText(err) } : previous); return; }
    displayTimeSaveInFlight.current = true;
    setBusy("save-display-time");
    try {
      await api<State>("/proactive/display-time", { method: "PATCH", body: JSON.stringify(patch) });
      await refresh(date, true);
      setDisplayTimeEditor(null);
      setNotice(patch.display_at ? `${careLabels[draft.type]} display time saved.` : `${careLabels[draft.type]} returned to automatic timing.`);
    } catch (err) {
      setDisplayTimeEditor((previous) => previous ? { ...previous, error: errorText(err), conflict: err instanceof ApiError && err.status === 409 } : previous);
    } finally {
      displayTimeSaveInFlight.current = false;
      setBusy("");
    }
  }
  async function refreshDisplayTimeDraft() {
    const draft = displayTimeEditor;
    if (!draft || busy) return;
    displayTimeSaveInFlight.current = true;
    setBusy("refresh-display-time");
    try {
      const snapshot = await refresh(date, true);
      const timing = snapshot?.assessment.types?.[draft.type]?.display_time;
      if (!timing) throw new Error("Display-time information could not be refreshed. Your draft is unchanged.");
      setDisplayTimeEditor({ ...rebaseDisplayTimeDraft(draft, timing), error: "", conflict: false,
        notice: `Latest saved display time: ${supportTimeLabels(timing).scheduled}. Your entries are unchanged; compare them before saving.` });
    } catch (err) {
      setDisplayTimeEditor((previous) => previous ? { ...previous, error: errorText(err) } : previous);
    } finally { displayTimeSaveInFlight.current = false; setBusy(""); }
  }
  function openRecording(recording: Recording) {
    setSource(null);
    setDrawerRecording(recording);
    setSelectedSegments([]);
    setSourceError("");
  }
  function openCard(card: Card) {
    if (card.memory_ids?.[0]) void openMemory(card.memory_ids[0]);
    else setNotice("This card has no valid sources to view right now.");
  }
  function openTopicEditor(recording: Recording, topic: Topic) {
    setTopicEditor({ ...createTopicDraft(recording, topic, state?.memories || []), error: "", conflict: false });
  }
  function editCardTopic(card: Card) {
    for (const recording of state?.recordings || []) {
      const topic = recording.topics?.find((item) => item.memory_ids.some((id) => card.memory_ids.includes(id)));
      if (topic) { openTopicEditor(recording, topic); return; }
    }
    openCard(card);
    setNotice("Topics are being organized automatically. Original sources remain available.");
  }
  async function saveTopic(event: FormEvent) {
    event.preventDefault();
    const draft = topicEditor;
    if (!draft || busy || draft.conflict) return;
    let body;
    try { body = topicPatch(draft); }
    catch (err) { setTopicEditor((previous) => previous ? { ...previous, error: errorText(err) } : previous); return; }
    topicSaveInFlight.current = true;
    setBusy("save-topic");
    try {
      const updated = await api<Recording>(`/recordings/${encodeURIComponent(draft.recordingId)}/topics/${encodeURIComponent(draft.topicId)}`, {
        method: "PATCH", body: JSON.stringify(body),
      });
      if (drawerRecording?.id === updated.id) setDrawerRecording(updated);
      await refresh(date, true);
      if (source?.recording.id === updated.id) await openMemory(source.memory.id);
      setTopicEditor(null);
      setNotice("Topic and atomic memories saved. Original audio, source fragments and speaker identity remain unchanged.");
    } catch (err) {
      setTopicEditor((previous) => previous ? { ...previous, conflict: err instanceof ApiError && err.status === 409,
        error: err instanceof ApiError && err.status === 409 ? "This topic or an atomic memory changed elsewhere. Your draft is preserved. Refresh saved values, compare them, then save explicitly." : errorText(err),
      } : previous);
    } finally { topicSaveInFlight.current = false; setBusy(""); }
  }
  async function refreshTopicDraft() {
    const draft = topicEditor;
    if (!draft || busy) return;
    topicSaveInFlight.current = true;
    setBusy("refresh-topic");
    try {
      const snapshot = await refresh(date, true);
      const recording = snapshot?.recordings.find((item) => item.id === draft.recordingId);
      const topic = recording?.topics?.find((item) => item.id === draft.topicId);
      if (!snapshot || !recording || !topic) throw new Error("This topic is no longer available. Your draft is still here; copy it before closing.");
      setTopicEditor((previous) => previous?.topicId === draft.topicId ? { ...previous,
        ...rebaseTopicDraft(previous, recording, topic, snapshot.memories), error: "", conflict: false,
        notice: "Saved values and versions are refreshed below. Your entries are unchanged; compare them before saving.",
      } : previous);
    } catch (err) { setTopicEditor((previous) => previous ? { ...previous, error: errorText(err) } : previous); }
    finally { topicSaveInFlight.current = false; setBusy(""); }
  }
  const scheduleBedtime = (card: Card, dueAt: string) =>
    run(
      `bedtime-${card.id}`,
      () =>
        api(`/proactive/${card.id}/bedtime`, {
          method: "POST",
          body: JSON.stringify({
            due_at: dueAt,
            expected_versions: card.versions,
          }),
        }),
      "Bedtime reminder set. The app must stay open for it to be checked when due.",
    );
  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    const uploadLimit = Number(health?.asr?.max_upload_bytes) / 1024 / 1024 || Number(health?.asr?.max_upload_mb) || 100;
    if (file.size > uploadLimit * 1024 * 1024) {
      setError(`The file exceeds ${uploadLimit} MB. Trim or compress it, then import again.`);
      return;
    }
    const body = new FormData();
    body.append("file", file);
    body.append("language", "en");
    setBusy("upload");
    setError("");
    try {
      const result = await api<{ duplicate?: boolean; recording: Recording }>(
        "/recordings",
        { method: "POST", body },
      );
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      await refresh("", true);
      setDate("");
      setNotice(
        result.duplicate
          ? "This recording was already imported; the existing source was kept and no duplicate evidence was added."
          : "Recording uploaded. Transcription, atomic memories and topic organization run automatically. Confirm which source segments are yours when they are ready.",
      );
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }
  async function confirmSegments() {
    const recording = activeRecording;
    const ids = sanitizeSelection(selectedSegments, pendingSegmentIds(segments));
    if (!recording || !ids.length || transcriptBusy) return;
    setBusy("confirm");
    setSourceError("");
    try {
      await api(`/recordings/${recording.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ segment_ids: ids }),
      });
      await refresh(date, true);
      setNotice("Selected segments confirmed as yours; memories and cards updated.");
      closeDrawer();
    } catch (err) {
      setSourceError(errorText(err));
    } finally {
      setBusy("");
    }
  }
  function deleteMemory(memory: Memory) {
    setDanger({
      title: "Delete this memory?",
      description:
        "This memory and its version history will be cleared, and related cards and suggestions will update. The original recording is kept; to remove the whole recording, go to the Import recording page.",
      run: async () => {
        await api(`/memories/${memory.id}`, { method: "DELETE" });
        closeDrawer();
      },
    });
  }
  function deleteRecording(recording: Recording) {
    setDanger({
      title: "Delete this recording and its memories?",
      description:
        "This removes the local audio file, transcript, related memories and history, and updates related cards. This cannot be undone.",
      run: async () => {
        await api(`/recordings/${recording.id}`, { method: "DELETE" });
        if (
          drawerRecording?.id === recording.id ||
          source?.recording.id === recording.id
        )
          closeDrawer();
      },
    });
  }
  async function understandRecording(recording: Recording) {
    await run(
      `understand-${recording.id}`,
      async () => {
        const next = await api<Recording>(`/recordings/${recording.id}/${recording.topic_pipeline?.status === "failed" ? "organize" : "understand"}`, {
          method: "POST",
        });
        if (drawerRecording?.id === recording.id) setDrawerRecording(next);
      },
      "Automatic processing retry started.",
    );
  }
  const feedback = (id: string, action: string) =>
    void run(
      `feedback-${id}`,
      () =>
        api(`/proactive/${id}/feedback`, {
          method: "POST",
          body: JSON.stringify({ action }),
        }),
      action === "done"
        ? "Item marked as done."
        : action === "snooze"
          ? "Snoozed. This only changes how the card is shown."
          : "Your feedback was recorded.",
    );
  const memories = (state?.memories || []).filter(
    (memory) => memory.status !== "deleted" && memory.status !== "superseded",
  );
  const filteredMemories = memories.filter(
    (memory) =>
      (filter === "all" ||
        memory.category === filter ||
        memory.status === filter) &&
      `${memory.content} ${memory.scope}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const recordings = (state?.recordings || []).filter(
    (recording) => recording.status !== "deleted",
  );
  const pending = recordings.filter(
    (recording) =>
      !recording.is_demo &&
      recording.segments?.some(
        (segment) => !isIdentityConfirmed(segment),
      ),
  );
  const activeMemories = memories.filter(
    (memory) => memory.status === "active",
  );
  const drawerVisible = Boolean(
    source || drawerRecording || sourceLoading || sourceError,
  );
  const selectedRecording = drawerRecording || source?.recording;
  const activeRecording = recordings.find(
    (recording) => recording.id === selectedRecording?.id,
  ) || selectedRecording;
  const segments = source?.segments || activeRecording?.segments || [];
  const selectableSegmentIds = pendingSegmentIds(segments);
  const effectiveSelection = sanitizeSelection(selectedSegments, selectableSegmentIds);
  const transcriptView = source
    ? { ...buildTranscriptView(segments), reason: "These are this memory's cited source fragments, shown without full-topic summaries." }
    : activeRecording ? buildRecordingTranscriptView(activeRecording) : buildTranscriptView(segments);
  const sourceTopic = source ? activeRecording?.topics?.find((topic) => topic.memory_ids.includes(source.memory.id)) : undefined;
  const transcriptBusy = Boolean(busy) || ["pending", "processing"].includes(activeRecording?.topic_pipeline?.status || "") || ["queued", "processing"].includes(activeRecording?.status || "")
    || ["pending", "processing"].includes(activeRecording?.memory_pipeline?.status || "");
  const selectionSourceKey = JSON.stringify([drawerVisible, source?.memory.id, activeRecording?.id, activeRecording?.created_at,
    segments.map((segment) => [getSegmentId(segment), segment.start, segment.end, segment.text,
      segment.speaker_label, segment.subject_id, segment.speaker, segment.confirmed])]);
  useEffect(() => {
    setSelectedSegments([]);
  }, [selectionSourceKey]);
  const asr = health?.asr;
  const hosted = health?.deployment?.mode === "server";
  const externalAsr = Boolean(asr?.external);
  const asrProvider = transcriptionProviderLabel(asr?.provider);
  const asrModel = String(asr?.model || health?.model || "Configured by the backend");
  const maxUploadMb = Number(asr?.max_upload_bytes) / 1024 / 1024 || Number(asr?.max_upload_mb) || 100;
  const memoryPipeline = health?.memory_pipeline;
  const modelStatus = transcriptionStageNames[String(asr?.state || "")] || (asr?.loaded
    ? externalAsr ? "ASR configured" : "Model loaded"
    : asr?.available
      ? externalAsr ? "ASR configured" : ["qwen", "sensevoice"].includes(String(asr?.provider)) ? "Loads on demand" : "Loads on first use"
      : health
        ? "Waiting for ASR setup"
        : "Connecting");
  const currentCards = state?.cards || [];
  const summaryStatus = summaryGenerationDisplay(state?.summary_generation);
  const showSummaryStatus = state?.summary_generation?.status !== "ready";
  const atomicSections = atomicTopicSections(recordings, filteredMemories);
  const careCards = state?.proactive || [];
  const careEmpty = careEmptyState(state?.care_generation, busy.startsWith("display-now-"));
  useEffect(() => {
    if (!supportResultToFocus) return;
    if (view !== "care") { setSupportResultToFocus(null); return; }
    if (careCards.some((card) => card.id === supportResultToFocus)
      && revealRequestedSupport(supportResultToFocus, (id) => document.getElementById(id))) {
      setSupportResultToFocus(null);
    }
  }, [supportResultToFocus, view, careCards]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            setView("daily");
          }}
          aria-label="Hear You Are home"
        >
          <span className="brand-mark">
            <Ear size={27} />
            <span />
          </span>
          <span>
            Hear You Are<small>Every day, heard</small>
          </span>
        </a>
        <div className="workspace-label">Your everyday space</div>
        <nav aria-label="Main navigation">
          <SidebarItem
            icon={<Layers3 size={19} />}
            label="Daily cards"
            isActive={view === "daily"}
            onClick={() => setView("daily")}
          />
          <SidebarItem
            icon={<AudioLines size={19} />}
            label="Import recording"
            isActive={view === "import"}
            onClick={() => setView("import")}
          />
          <SidebarItem
            icon={<Database size={19} />}
            label="Atomic memories"
            isActive={view === "memory"}
            onClick={() => setView("memory")}
          />
          <SidebarItem
            icon={<Sparkles size={19} />}
            label="Proactive AI"
            isActive={view === "care"}
            onClick={() => setView("care")}
          />
        </nav>
        <div className="sidebar-note">
          <div className="note-line">
            <LockKeyhole size={16} />
            <strong>Your memories, in your hands</strong>
          </div>
          <p>Revisit the original words, correct the understanding, or delete anything you do not want to keep.</p>
        </div>
        <div className="sidebar-bottom">
          <button
            className="settings-nav"
            onClick={() => setShowSettings(true)}
          >
            <Settings2 size={18} />
            Preferences & model
            <span className="status-dot" />
          </button>
          <div className="profile">
            <span className="avatar">You</span>
            <div>
              <strong>My everyday</strong>
              <small>{hosted ? "Hosted demo · Shared workspace" : "Local app · MVP"}</small>
            </div>
            <span className="profile-dot" />
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            My space<span>/</span>
            <strong>{viewNames[view]}</strong>
          </div>
          <div className="topbar-actions">
            <span className="local-badge">
              <span
                className={`status-dot ${error && !state ? "offline" : ""}`}
              />
              {error && !state
                ? "Service offline"
                : hosted
                  ? "Running on server"
                  : "Running locally"}
            </span>
            <button
              className="icon-button"
              aria-label="Open preferences"
              onClick={() => setShowSettings(true)}
            >
              <Settings2 size={18} />
            </button>
          </div>
        </header>
        <div className="page-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow heading-eyebrow">
                {view === "daily"
                  ? "Turn scattered moments into understanding"
                  : view === "import"
                    ? "Start with a recording"
                    : view === "memory"
                      ? "Every understanding has a source"
                      : "A little support, at the right time"}
              </div>
              <h1>
                {viewNames[view]}
                <span className="heading-sparkle">
                  {view === "daily" ? <Sparkles size={24} /> : null}
                </span>
              </h1>
              <p>
                {view === "daily"
                  ? "Look back on moods, connections and moments worth remembering. You can confirm or correct them."
                  : view === "import"
                    ? "Upload an English recording. Transcription, atomic memories and topics are prepared automatically; then confirm which source segments are yours."
                    : view === "memory"
                      ? "See the original words and versions behind each memory, and bring it closer to what really happened."
                      : "Reminders, suggestions, comfort and warnings appear when their source conditions, your preferences and display settings allow."}
              </p>
            </div>
            <div className="heading-actions">
              {view !== "import" && (
                <button
                  className="button primary"
                  onClick={() => setView("import")}
                >
                  <Plus size={18} />
                  Import recording
                </button>
              )}
            </div>
          </div>
          {error && (
            <div className="alert alert-error" role="alert">
              <span>{error}</span>
              <button className="text-button" onClick={() => void refresh()}>
                Retry <RefreshCw size={15} />
              </button>
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="alert alert-success" role="status">
              <CheckCircle2 size={18} />
              <span>{notice}</span>
              <button
                className="icon-button"
                aria-label="Dismiss notification"
                onClick={() => setNotice("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {loading && !state ? (
            <div className="loading-state" role="status">
              <LoaderCircle className="spin" size={30} />
              <p>Preparing your space…</p>
            </div>
          ) : (
            <>
              {(view === "daily" || view === "care") && (
                <div className="date-row">
                  <div className="date-selector">
                    <button
                      className="icon-button"
                      aria-label="Previous day"
                      onClick={() => {
                        const next = new Date(`${selectedDate}T12:00:00+08:00`);
                        next.setUTCDate(next.getUTCDate() - 1);
                        setDate(next.toISOString().slice(0, 10));
                      }}
                    >
                      <ChevronLeft size={16} />
                    </button>
                    <label>
                      <CalendarDays size={17} />
                      <input
                        aria-label="Choose Uploading date"
                        type="date"
                        value={selectedDate}
                        onChange={(event) => setDate(event.target.value)}
                      />
                    </label>
                    <button
                      className="icon-button"
                      aria-label="Next day"
                      onClick={() => {
                        const next = new Date(`${selectedDate}T12:00:00+08:00`);
                        next.setUTCDate(next.getUTCDate() + 1);
                        setDate(next.toISOString().slice(0, 10));
                      }}
                    >
                      <ChevronRight size={16} />
                    </button>
                  </div>
                  <span className="date-description">
                    Uploading date · {niceDate(selectedDate)}
                    {selectedDate === localDate() && (
                      <span className="tag">Today</span>
                    )}
                  </span>
                  <button
                    className="refresh-button"
                    disabled={Boolean(busy)}
                    onClick={() => void refresh(date, true)}
                  >
                    <RefreshCw size={14} />
                    Refresh
                  </button>
                </div>
              )}
              {view === "daily" && (
                <>
                  <section className="welcome-banner">
                    <div className="banner-copy">
                      <span className="banner-label">
                        <Sprout size={15} />
                        Give yourself some space
                      </span>
                      <h2>Your day deserves to be heard.</h2>
                      <p>
                        {currentCards.length
                          ? "These summaries keep their original sources. Edit a topic or atomic memory whenever needed."
                          : "You don't need to remember every word. Import a recording and let the moments that matter surface gently."}
                      </p>
                      <div className="banner-status">
                        <span>
                          <AudioLines size={14} />
                          {recordings.length} recording{recordings.length === 1 ? "" : "s"}
                        </span>
                        <span>
                          <CheckCircle2 size={14} />
                          {activeMemories.length} active {activeMemories.length === 1 ? "memory" : "memories"}
                        </span>
                      </div>
                    </div>
                    <div className="banner-art" aria-hidden="true">
                      <div className="orbit orbit-one" />
                      <div className="orbit orbit-two" />
                      <div className="art-leaf leaf-one" />
                      <div className="art-leaf leaf-two" />
                      <div className="art-leaf leaf-three" />
                      <div className="art-stem" />
                      <div className="art-spark spark-one">✦</div>
                      <div className="art-spark spark-two">✧</div>
                      <div className="art-caption">Notice · Understand · Grow</div>
                    </div>
                  </section>
                  {pending.length > 0 && (
                    <div className="pending-banner">
                      <MessageCircle size={19} />
                      <div>
                        <strong>{pending.length} recording{pending.length === 1 ? "" : "s"} awaiting your confirmation</strong>
                        <span>
                          Transcription finished. Confirm which segments are yours so they can be used in cards.
                        </span>
                      </div>
                      <button
                        className="text-button"
                        onClick={() => openRecording(pending[0])}
                      >
                        View segments <ArrowRight size={16} />
                      </button>
                    </div>
                  )}
                  <section>
                    <div className="section-heading">
                      <h2>
                        Daily moments
                        <span className="section-count">
                          {currentCards.length.toString().padStart(2, "0")}
                        </span>
                      </h2>
                      <span>Only covers imported segments · Editable</span>
                    </div>
                    {showSummaryStatus && <div className="assessment summary-status" role={summaryStatus.canRetry ? "alert" : "status"}>
                      {summaryStatus.polling ? <LoaderCircle className="spin" size={18} /> : <FileAudio size={18} />}
                      <div><strong>{summaryStatus.label}</strong><p>{summaryStatus.message}</p></div>
                      {summaryStatus.canRetry && <button className="small-button" disabled={Boolean(busy)} onClick={() => void run("retry-summary", () => api("/analysis/generate", { method: "POST", body: JSON.stringify({ date: selectedDate }) }), "Daily summaries updated.")}>{busy === "retry-summary" ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}Retry summary</button>}
                    </div>}
                    {currentCards.length ? (
                      <div className="daily-grid">
                        {currentCards.map((card) => (
                          <DailyCard
                            key={card.id}
                            card={card}
                            generation={state?.summary_generation}
                            open={() => openCard(card)}
                            edit={() => editCardTopic(card)}
                            busy={Boolean(busy)}
                          />
                        ))}
                      </div>
                    ) : (
                      <div className="empty-cards">
                        {(["mood", "social", "moments"] as Category[]).map(
                          (category) => {
                            const Icon = categoryIcons[category];
                            return (
                              <div
                                className={`placeholder-card category-${category}`}
                                key={category}
                              >
                                <span className="category-icon">
                                  <Icon size={22} />
                                </span>
                                <h3>{categoryLabels[category]}</h3>
                                <p>
                                  {category === "mood"
                                    ? "Notice how you describe your feelings"
                                    : category === "social"
                                      ? "Capture everyday conversations and connections"
                                      : "Keep the things and promises that matter to you"}
                                </p>
                                <div className="placeholder-lines">
                                  <i />
                                  <i />
                                </div>
                                <span className="placeholder-note">
                                  Import and confirm a recording to get started
                                </span>
                              </div>
                            );
                          },
                        )}
                      </div>
                    )}
                  </section>
                  <section className="daily-bottom">
                    <div className="care-preview">
                      <div className="section-heading">
                        <h2>
                          <Sparkles size={19} />
                          Care right now
                        </h2>
                        <button
                          className="text-button"
                          onClick={() => setView("care")}
                        >
                          View all <ArrowRight size={15} />
                        </button>
                      </div>
                      {careCards.length ? (
                        <CareCard
                          card={careCards[0]}
                          feedback={feedback}
                          open={() => openCard(careCards[0])}
                          memories={memories}
                          recordings={recordings}
                          openMemory={(id) => void openMemory(id)}
                          schedule={scheduleBedtime}
                          busy={Boolean(busy)}
                        />
                      ) : (
                        <div className="quiet-card">
                          <span>
                            <Heart size={23} />
                          </span>
                          <div>
                            <h3>Nothing needs to interrupt you right now</h3>
                            <p>
                              {state?.assessment?.reason ||
                                "Care appears here only when its conditions are met."}
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="how-card">
                      <div className="section-heading">
                        <h2>From recording to understanding</h2>
                        <Layers3 size={18} />
                      </div>
                      <div className="mini-flow">
                        <span>
                          <FileAudio size={17} />
                          <small>Words</small>
                        </span>
                        <ArrowRight size={14} />
                        <span>
                          <Database size={17} />
                          <small>Memory</small>
                        </span>
                        <ArrowRight size={14} />
                        <span>
                          <Heart size={17} />
                          <small>Card</small>
                        </span>
                      </div>
                      <p>Every card shows its sources. Correct one detail and related understanding updates too.</p>
                      <button
                        className="text-button"
                        onClick={() => setView("memory")}
                      >
                        Manage my memories <ArrowRight size={15} />
                      </button>
                    </div>
                  </section>
                </>
              )}
              {view === "import" && (
                <>
                  <form onSubmit={upload} className="upload-card">
                    <div
                      className={`drop-zone ${dragging ? "is-dragging" : ""}`}
                      onDragOver={(event) => {
                        event.preventDefault();
                        setDragging(true);
                      }}
                      onDragLeave={() => setDragging(false)}
                      onDrop={(event) => {
                        event.preventDefault();
                        setDragging(false);
                        const next = event.dataTransfer.files[0];
                        if (next) setFile(next);
                      }}
                    >
                      <span className="upload-symbol">
                        <UploadCloud size={32} />
                      </span>
                      <h2>{file ? file.name : "Bring a recording into your space"}</h2>
                      <p>
                        {file
                          ? `${(file.size / 1024 / 1024).toFixed(1)} MB · Ready to import`
                          : "Drop a file here, or choose a recording from your computer"}
                      </p>
                      <input
                        ref={inputRef}
                        className="visually-hidden"
                        id="audio-file"
                        type="file"
                        accept=".mp3,.wav,.m4a,.flac,.ogg,.webm,.mp4,audio/*"
                        onChange={(event) =>
                          setFile(event.target.files?.[0] || null)
                        }
                      />
                      <button
                        className="button secondary"
                        type="button"
                        onClick={() => inputRef.current?.click()}
                      >
                        <Plus size={16} />
                        {file ? "Choose another" : "Choose a recording"}
                      </button>
                      <small>
                        MP3, WAV, M4A, FLAC, OGG, WebM · Up to {maxUploadMb} MB / 20 minutes
                        {(Number(asr?.required_channels) === 1 || Boolean(asr?.mono_only)) && " · Mono audio required"}
                      </small>
                    </div>
                    {hosted && (
                      <p className="small-text muted">
                        Recordings are uploaded to and stored on this demo server.
                        Others with demo access can view them; users do not have
                        separate private workspaces.
                      </p>
                    )}
                    {externalAsr && (
                      <p className="small-text muted">
                        Audio is sent to {asrProvider} for transcription{asr?.speaker_diarization ? " and anonymous speaker separation" : ""}.
                        Speaker labels do not identify you; you confirm your own segments.
                      </p>
                    )}
                    {asr && !externalAsr && (
                      <p className="small-text muted">
                        Audio is processed {hosted ? "on this demo server" : "on this device"}.
                        {asr.speaker_diarization
                          ? " Anonymous labels distinguish voices within this recording; you confirm which segments are yours."
                          : " Automatic speaker separation is not enabled; speakers stay unknown until you confirm your segments."}
                      </p>
                    )}
                    {asr?.provider === "sensevoice" && (
                      <p className="small-text muted">
                        Playback times follow estimated speech and speaker segments. Word timestamps are not available.
                      </p>
                    )}
                    {memoryPipeline?.mode === "deepseek" && (
                      <p className="small-text muted">
                        {memoryTransferNotice(memoryPipeline.provider)}{" "}
                        Processing starts automatically; speaker identity and your support preferences remain separate.
                      </p>
                    )}
                    <div className="upload-options">
                      <div>
                        <span className="field-label">Transcription language</span>
                        <div className="language-field">
                          English <span className="tag">English first</span>
                        </div>
                        <small>
                          Uploading date is assigned by the server when the file is uploaded · Hong Kong (UTC+8).
                        </small>
                      </div>
                      <button
                        className="button primary upload-submit"
                        type="submit"
                        disabled={!file || busy === "upload"}
                      >
                        {busy === "upload" ? (
                          <LoaderCircle className="spin" size={17} />
                        ) : (
                          <ArrowDownToLine size={17} />
                        )}{" "}
                        {busy === "upload" ? "Uploading…" : "Upload and process"}
                      </button>
                    </div>
                  </form>
                  <div className="process-explainer">
                    {[
                      ["1", externalAsr ? "Cloud English transcription" : hosted ? "Server-side English transcription" : "Local English transcription", `${asrProvider} · ${asrModel}`],
                      ["2", "Atomic memories", "Extracted automatically with source links"],
                      ["3", "Topics", "Related atomic memories are organized automatically"],
                    ].map(([n, title, text]) => (
                      <div key={n}>
                        <span>{n}</span>
                        <div>
                          <strong>{title}</strong>
                          <small>{text}</small>
                        </div>
                      </div>
                    ))}
                  </div>
                  <section>
                    <div className="section-heading">
                      <h2>
                        Recordings
                        <span className="section-count">
                          {recordings.length}
                        </span>
                      </h2>
                      <span>{modelStatus}</span>
                    </div>
                    {recordings.length ? (
                      <div className="recordings-list">
                        {recordings.map((recording) => (
                          <article className="recording-row" key={recording.id}>
                            <span className="file-icon">
                              <FileAudio size={23} />
                            </span>
                            <div className="recording-info">
                              <h3>{recording.filename}</h3>
                              <p>{uploadingDateLabel(recording)}</p>
                              {recording.error && (
                                <p className="error-text">{englishMessage(recording.error)}</p>
                              )}
                              <PipelineProgress pipeline={recording.memory_pipeline} recording={recording} />
                            </div>
                            <span
                              className={`status-pill status-${recording.status}`}
                            >
                              {[
                                "processing",
                                "transcribing",
                                "queued",
                              ].includes(recording.status) && (
                                <LoaderCircle size={13} className="spin" />
                              )}
                              {statusNames[recording.status] ||
                                recording.status}
                            </span>
                            <div className="recording-actions">
                              {["failed", "error"].includes(
                                recording.status,
                              ) && (
                                <button
                                  className="small-button"
                                  disabled={Boolean(busy)}
                                  onClick={() =>
                                    void run(
                                      `retry-${recording.id}`,
                                      () =>
                                        api(
                                          `/recordings/${recording.id}/retry`,
                                          { method: "POST" },
                                        ),
                                      "Transcription has been re-queued.",
                                    )
                                  }
                                >
                                  <RefreshCw size={14} />
                                  Retry
                                </button>
                              )}
                              {Boolean(recording.segments?.length) && (
                                <>
                                  {(recording.memory_pipeline?.status === "failed" || recording.topic_pipeline?.status === "failed") && <button
                                    className="small-button"
                                    disabled={Boolean(busy) || !memoryPipeline?.configured || ["pending", "processing"].includes(recording.memory_pipeline?.status || "")}
                                    title={memoryPipeline?.configured ? "Process transcript with DeepSeek" : "Configure DeepSeek on the server first"}
                                    onClick={() => void understandRecording(recording)}
                                  >
                                    <Sparkles size={14} />
                                    Retry automatic processing
                                  </button>}
                                  <button
                                    className="small-button"
                                    onClick={() => openRecording(recording)}
                                  >
                                    View transcript
                                  </button>
                                </>
                              )}
                              <button
                                className="icon-button"
                                aria-label={`Delete recording ${recording.filename}`}
                                onClick={() => deleteRecording(recording)}
                              >
                                <Trash2 size={16} />
                              </button>
                            </div>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className="panel">
                        <Empty
                          icon={<FileAudio size={26} />}
                          title="Your recordings will be kept here"
                        >
                          Choose a recording and its processing status and transcript will appear here.
                        </Empty>
                      </div>
                    )}
                  </section>
                </>
              )}
              {view === "memory" && <>
                <p className="ai-section-description">Atomic memories are organized by topic. Edit a topic and its memories together; original source words stay unchanged.</p>
                <div className="memory-toolbar">
                  <label className="search-field"><Search size={17} /><input placeholder="Search atomic memory content" aria-label="Search atomic memories" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
                  <select aria-label="Filter atomic memories" value={filter} onChange={(event) => setFilter(event.target.value)}>
                    <option value="all">All atomic memories</option><option value="mood">Mood &amp; feelings</option><option value="social">Social &amp; connection</option><option value="moments">Memorable moments</option><option value="pending">Speaker unconfirmed</option>
                  </select><span className="result-count">{filteredMemories.length} atomic memories</span>
                </div>
                {atomicSections.length ? <div className="atomic-topic-list">{atomicSections.map((section) => <section className="atomic-topic-card" key={`${section.recording.id}-${section.topic?.id || "pending"}`}>
                  <div className="atomic-topic-heading"><div><h2>{section.number ? `${section.number}. ` : ""}{section.title}</h2><p>{section.recording.filename}</p><p>{uploadingDateLabel(section.recording)}</p></div>
                    <div className="atomic-topic-actions">{section.topic && <button className="small-button" disabled={Boolean(busy)} onClick={() => openTopicEditor(section.recording, section.topic!)}>Edit topic</button>}<button className="text-button" onClick={() => openRecording(section.recording)}>Original sources <ArrowRight size={14} /></button></div>
                  </div>
                  {section.summary && <p className="atomic-topic-summary">{section.summary}</p>}
                  {!section.topic && <p className="source-help">Topic organization runs automatically. These atomic memories remain visible while it completes.</p>}
                  <div className="memory-list">{section.memories.map((memory, index) => <article className="memory-row" key={memory.id}>
                    <span className="atomic-memory-number">{section.number ? `${section.number}.${(section.topic?.memory_ids.indexOf(memory.id) ?? index) + 1}` : index + 1}</span>
                    <div><div className="memory-row-meta"><span>{categoryLabels[memory.category]}</span><span>v{memory.version}</span><span>{memory.subject_id === "user" && memory.confirmed ? "Your speaker identity confirmed" : "Speaker unconfirmed"}</span></div><h3>{memory.content}</h3></div>
                    <button className="text-button" onClick={() => void openMemory(memory.id)}>Sources <ArrowRight size={15} /></button>
                  </article>)}</div>
                </section>)}</div> : <div className="panel"><Empty icon={<Database size={27} />} title={query || filter !== "all" ? "No matching atomic memories" : "Atomic memories appear automatically after upload"}>{query || filter !== "all" ? "Try another search or filter." : "Upload a recording. Transcribing, atomic memory extraction and topic organization run automatically."}</Empty></div>}
              </>}
              {view === "care" && (
                <>
                  <div className="section-heading ai-section-heading"><h2><Sparkles size={19} />Proactive support</h2><span>Preferences and conditions apply automatically</span></div>
                  <div className="care-intro">
                    <span className="care-intro-icon">
                      <Heart size={26} />
                    </span>
                    <div>
                      <h2>Support with evidence, at your pace.</h2>
                      <p>
                        Support updates automatically from your confirmed memories, preferences and display settings. When the evidence is unclear, support stays quiet.
                      </p>
                    </div>
                    <button
                      className="button secondary"
                      onClick={() =>
                        void run(
                          "evaluate",
                          () =>
                            api("/proactive/evaluate", {
                              method: "POST",
                              body: JSON.stringify({ date: selectedDate }),
                            }),
                          "Current conditions re-checked.",
                        )
                      }
                      disabled={Boolean(busy)}
                    >
                      {busy === "evaluate" ? (
                        <LoaderCircle className="spin" size={16} />
                      ) : (
                        <RefreshCw size={16} />
                      )}
                      Refresh conditions
                    </button>
                  </div>
                  <div className="condition-grid">
                    {(
                      ["reminder", "suggestion", "comfort", "warning"] as CareType[]
                    ).map((type) => {
                      const condition = careConditionDisplay(type, state?.assessment, state?.settings?.preferences);
                      const timing = state?.assessment.types?.[type]?.display_time;
                      const timeLabels = supportTimeLabels(timing);
                      return (
                      <div
                        className={`condition-tile ${condition.matched ? "matched" : ""}`}
                        key={type}
                      >
                        <div>
                          <strong>{careLabels[type]}</strong>
                          <span>{condition.badge}</span>
                        </div>
                        <p className="condition-diagnostic" aria-label={`${careLabels[type]} assessment`}>{condition.reason}</p>
                        {condition.deliveryReason && condition.deliveryReason !== condition.reason && <p className="condition-delivery" aria-label={`${careLabels[type]} delivery`}><strong>Delivery:</strong> {condition.deliveryReason}</p>}
                        <details className="condition-requirement"><summary>Support requirements</summary><p>{condition.requirement}</p></details>
                        <dl className="support-display-times">
                          <dt>Scheduled display (not before)</dt><dd>{timeLabels.scheduled}</dd>
                          <dt>Earliest automatic display</dt><dd>{timeLabels.next}</dd>
                          <dt>Last displayed</dt><dd>{timeLabels.displayed}</dd>
                        </dl>
                        {timeLabels.reason !== condition.reason && timeLabels.reason !== condition.deliveryReason && <p className="support-time-reason">{timeLabels.reason}</p>}
                        <div className="support-display-actions">
                          <button className="small-button ghost" disabled={Boolean(busy) || !timing} onClick={() => openDisplayTimeEditor(type)} aria-label={`Edit display time for ${careLabels[type]}`}>Edit display time</button>
                          <button className="small-button" disabled={Boolean(busy) || !condition.canRequestSupport} onClick={() => void requestSupport(type)} aria-label={`Display Proactive Support Now: ${careLabels[type]}`}>{busy === `display-now-${type}` && <LoaderCircle className="spin" size={14} />}{busy === `display-now-${type}` ? "Preparing support…" : "Display Proactive Support Now"}</button>
                        </div>
                      </div>
                    );})}
                  </div>
                  <div className="assessment">
                    <ShieldCheck size={18} />
                    <div>
                      <strong>This condition check</strong>
                      <p>
                        {state?.assessment?.reason ||
                          "No valid memories to evaluate yet."}
                      </p>
                      <p>Automatic support also checks quiet mode, timing and frequency. Display Now requests one eligible card without changing your saved schedule or preferences.</p>
                    </div>
                  </div>
                  {state?.care_generation && ["pending", "failed", "unconfigured"].includes(state.care_generation.status) && <div className="assessment" role={state.care_generation.status === "failed" ? "alert" : "status"}>
                    {state.care_generation.status === "pending" ? <LoaderCircle className="spin" size={18} /> : <Heart size={18} />}
                    <div><strong>{state.care_generation.status === "pending" ? "Preparing support" : "Support unavailable"}</strong><p>{state.care_generation.message}</p></div>
                  </div>}
                  {careCards.length ? (
                    <div className="care-grid">
                      {careCards.map((card) => (
                        <CareCard
                          key={card.id}
                          card={card}
                          feedback={feedback}
                          open={() => openCard(card)}
                          memories={memories}
                          recordings={recordings}
                          openMemory={(id) => void openMemory(id)}
                          schedule={scheduleBedtime}
                          busy={Boolean(busy)}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="panel">
                      <Empty
                        icon={<Heart size={27} />}
                        title={careEmpty.title}
                      >
                        {careEmpty.message}
                      </Empty>
                    </div>
                  )}
                </>
              )}
              <footer className="page-footer">
                <span>
                  <ShieldCheck size={13} />
                  Traceable sources, correctable understanding
                </span>
                <span>
                  Analysis mode:{" "}
                  {health?.analysis?.mode === "llm"
                    ? "Configured model analysis"
                    : health?.memory_pipeline?.mode === "deepseek" ? "DeepSeek setup required" : "Local rule-based demo"}{" "}
                  · No clinical diagnosis
                </span>
                <a
                  href="https://github.com/kenlacroix/moodhaven-journal"
                  target="_blank"
                  rel="noreferrer"
                >
                  UI components: MoodHaven · MIT
                </a>
              </footer>
            </>
          )}
        </div>
      </main>
      {displayTimeEditor && <Modal title={`Edit display time · ${careLabels[displayTimeEditor.type]}`} close={closeDisplayTimeEditor} className="display-time-modal">
        <form className="modal-body" onSubmit={saveDisplayTime}>
          <p className="modal-description">Choose a one-time earliest display time in Hong Kong (UTC+8). Source conditions, preferences and automatic delivery checks still apply while the app is open.</p>
          <label className="display-time-choice"><input type="radio" name="display-time-mode" checked={displayTimeEditor.automatic} disabled={Boolean(busy)} onChange={() => setDisplayTimeEditor((previous) => previous ? { ...previous, automatic: true, error: previous.conflict ? previous.error : "" } : previous)} />Automatic timing</label>
          <label className="display-time-choice"><input type="radio" name="display-time-mode" checked={!displayTimeEditor.automatic} disabled={Boolean(busy)} onChange={() => setDisplayTimeEditor((previous) => previous ? { ...previous, automatic: false, error: previous.conflict ? previous.error : "" } : previous)} />Set one display time</label>
          {!displayTimeEditor.automatic && <label className="field-label" htmlFor="support-display-time">Hong Kong date and time (UTC+8)<input id="support-display-time" type="datetime-local" required value={displayTimeEditor.value} disabled={Boolean(busy)} onChange={(event) => setDisplayTimeEditor((previous) => previous ? { ...previous, value: event.target.value, error: previous.conflict ? previous.error : "" } : previous)} /></label>}
          {displayTimeEditor.notice && <p className="topic-refresh-notice" role="status">{displayTimeEditor.notice}</p>}
          {displayTimeEditor.error && <div className="date-editor-error" role="alert"><p>{displayTimeEditor.error}</p>{displayTimeEditor.conflict && <button type="button" className="small-button" disabled={Boolean(busy)} onClick={() => void refreshDisplayTimeDraft()}><RefreshCw size={14} />Refresh saved time; keep my draft</button>}</div>}
          <div className="date-editor-actions"><button type="button" className="button secondary" disabled={Boolean(busy)} onClick={closeDisplayTimeEditor}>Cancel</button><button type="submit" className="button primary" disabled={Boolean(busy) || displayTimeEditor.conflict || (!displayTimeEditor.automatic && !displayTimeEditor.value)}>{busy === "save-display-time" ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}Save display time</button></div>
        </form>
      </Modal>}
      {showSettings && state && (
        <Preferences
          error={error}
          settings={state.settings}
          health={health}
          busy={Boolean(busy)}
          close={closeSettings}
          save={(patch) =>
            void run(
              "settings",
              () =>
                api("/settings", {
                  method: "PATCH",
                  body: JSON.stringify(patch),
                }),
              "Preferences updated; pending support was re-checked.",
            )
          }
        />
      )}
      {topicEditor && <Modal title={`Edit topic ${topicEditor.number}`} close={closeTopicEditor} className="topic-edit-modal">
        <form className="modal-body topic-edit-form" onSubmit={saveTopic}>
          <p className="modal-description">{topicEditor.recordingName}<br />Edit the topic and its atomic memories. Original audio, transcript fragments, speaker identity and support permissions stay unchanged.</p>
          <label htmlFor="topic-edit-title" className="field-label">Topic title</label>
          <input id="topic-edit-title" type="text" maxLength={120} value={topicEditor.title} disabled={Boolean(busy)} onChange={(event) => setTopicEditor((previous) => previous ? { ...previous, title: event.target.value, error: previous.conflict ? previous.error : "" } : previous)} />
          <label htmlFor="topic-edit-summary" className="field-label">Topic summary</label>
          <textarea id="topic-edit-summary" rows={3} maxLength={2000} value={topicEditor.summary} disabled={Boolean(busy)} onChange={(event) => setTopicEditor((previous) => previous ? { ...previous, summary: event.target.value, error: previous.conflict ? previous.error : "" } : previous)} />
          {topicEditor.notice && <div className="topic-refresh-notice" role="status"><p>{topicEditor.notice}</p><details><summary>Latest saved topic text</summary><strong>{topicEditor.savedTitle}</strong><p>{topicEditor.savedSummary}</p></details></div>}
          <h3 className="small-heading">Atomic memories</h3>
          {!topicEditor.memories.length && <p className="source-help">No atomic memories are linked to this topic yet. You can still edit its title and summary.</p>}
          {topicEditor.memories.map((memory, index) => <div className="topic-memory-field" key={memory.id}>
            <label className="field-label" htmlFor={`topic-memory-${index}`}>{topicEditor.number}.{index + 1} · Atomic memory · v{memory.version}</label>
            <textarea id={`topic-memory-${index}`} rows={3} maxLength={4000} value={memory.content} disabled={Boolean(busy)} onChange={(event) => setTopicEditor((previous) => previous ? { ...previous, memories: previous.memories.map((item) => item.id === memory.id ? { ...item, content: event.target.value } : item), error: previous.conflict ? previous.error : "" } : previous)} />
            {memory.unavailable && <p className="error-text">This atomic memory is unavailable. Your draft is preserved; copy it before closing.</p>}
            {topicEditor.notice && <details className="topic-saved-memory"><summary>Latest saved atomic memory</summary><p>{memory.savedContent}</p></details>}
          </div>)}
          {topicEditor.error && <div className="date-editor-error" role="alert"><p>{topicEditor.error}</p>{topicEditor.conflict && <button type="button" className="small-button" disabled={Boolean(busy)} onClick={() => void refreshTopicDraft()}><RefreshCw size={14} />Refresh saved values; keep my draft</button>}</div>}
          <div className="date-editor-actions"><button type="button" className="button secondary" disabled={Boolean(busy)} onClick={closeTopicEditor}>Cancel</button><button type="submit" className="button primary" disabled={Boolean(busy) || topicEditor.conflict || !topicDraftChanged(topicEditor) || topicEditor.memories.some((memory) => memory.unavailable)}>{busy === "save-topic" ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}Save changes</button></div>
        </form>
      </Modal>}
      {drawerVisible && !topicEditor && (
        <Modal
          title={source ? "Memory & original words" : "Recording transcript"}
          close={closeDrawer}
          className="source-drawer"
        >
          <div className="drawer-content">
            {sourceLoading ? (
              <div className="loading-state">
                <LoaderCircle className="spin" size={24} />
                <p>Loading sources…</p>
              </div>
            ) : (
              <>
                {sourceError && (
                  <div className="alert alert-error" role="alert">
                    <span>{sourceError}</span>
                  </div>
                )}
                {source && (
                  <div className="source-memory">
                    <div className="source-label">
                      <span className="tag">
                        {categoryLabels[source.memory.category]}
                      </span>
                      <span>Memory v{source.memory.version}</span>
                    </div>
                    <h3>{source.memory.content}</h3>
                    <div className="evidence-label">
                      <ShieldCheck size={14} />
                      {source.memory.confirmed
                        ? "Speaker confirmed"
                        : "Awaiting speaker confirmation"}{" "}
                      ·{" "}
                      {source.memory.evidence_kind === "ai_inference"
                        ? "System inference"
                        : source.memory.evidence_kind === "user_confirmed"
                          ? "User addition / correction"
                          : "Original statement"}
                    </div>
                    <div className="source-topic-actions">
                      {sourceTopic && activeRecording && <button className="small-button" disabled={Boolean(busy)} onClick={() => openTopicEditor(activeRecording, sourceTopic)}>Edit topic {sourceTopic.number}</button>}
                      <button className="text-button danger-text" disabled={Boolean(busy)} onClick={() => deleteMemory(source.memory)}>Delete atomic memory</button>
                    </div>
                  </div>
                )}
                {activeRecording && (
                  <section className="source-section">
                    <div className="source-section-title">
                      <h3>
                        <FileAudio size={17} />
                        Original recording
                      </h3>
                      <span>{activeRecording.filename}</span>
                    </div>
                    <p className="source-help">{uploadingDateLabel(activeRecording)}</p>
                    <audio
                      ref={audioRef}
                      controls
                      preload="metadata"
                      src={`/api/recordings/${activeRecording.id}/audio`}
                      onError={() =>
                        setSourceError(
                          "The recording could not be loaded. The file may have been removed; refresh the sources.",
                        )
                      }
                      aria-label="Original recording playback"
                    />
                    <PipelineProgress pipeline={activeRecording.memory_pipeline} recording={activeRecording} />
                    {Boolean(activeRecording.segments?.length) && (activeRecording.memory_pipeline?.status === "failed" || activeRecording.topic_pipeline?.status === "failed") && (
                      <button
                        className="small-button"
                        disabled={Boolean(busy) || !memoryPipeline?.configured || ["pending", "processing"].includes(activeRecording.memory_pipeline?.status || "")}
                        title={memoryPipeline?.configured ? "Process transcript with DeepSeek" : "Configure DeepSeek on the server first"}
                        onClick={() => void understandRecording(activeRecording)}
                      >
                        <Sparkles size={14} />
                        Retry automatic processing
                      </button>
                    )}
                  </section>
                )}
                {segments.length > 0 && (
                  <section className="source-section">
                    <div className="source-section-title">
                      <h3>
                        <MessageCircle size={17} />
                        {transcriptView.kind === "topics" ? "Transcript by topic" : "Original source turns"}
                      </h3>
                      <span>Original words preserved</span>
                    </div>
                    <p className="source-help">{transcriptView.reason}</p>
                    {selectableSegmentIds.length > 0 ? (
                      <>
                        <p className="source-help" id="transcript-selection-help">
                          Select only the original fragments that are definitely yours. Anonymous speaker labels do not identify you.
                          Selecting a topic or all fragments does not confirm anything; review other voices and unknown speech before continuing.
                        </p>
                        <div className="transcript-selection-bar">
                          <SelectionCheckbox label="Select all" checked={effectiveSelection.length === selectableSegmentIds.length}
                            indeterminate={effectiveSelection.length > 0 && effectiveSelection.length < selectableSegmentIds.length}
                            disabled={transcriptBusy}
                            onChange={(checked) => setSelectedSegments((previous) => toggleIds(previous, selectableSegmentIds, checked, selectableSegmentIds))} />
                          <span role="status" aria-live="polite">{effectiveSelection.length} of {selectableSegmentIds.length} pending fragments selected</span>
                          <button className="text-button" disabled={transcriptBusy || !effectiveSelection.length}
                            onClick={() => setSelectedSegments([])}>Clear selection</button>
                        </div>
                      </>
                    ) : <p className="source-help">No unconfirmed fragments are available to select. Speaker identity remains separate from topic edits and support preferences.</p>}
                    <div className="transcript-topics">
                      {transcriptView.groups.map((group, groupIndex) => {
                        const pendingIds = pendingSegmentIds(group.segments);
                        const count = pendingIds.filter((id) => effectiveSelection.includes(id)).length;
                        return (
                          <article className="transcript-topic" key={`${activeRecording?.id}-${group.id}`}>
                            <div className="topic-heading">
                              <h4>{transcriptView.kind === "topics" ? `${group.number || groupIndex + 1}. ` : ""}{group.title}</h4>
                              <span className="topic-kind">{group.topic?.edited ? "Edited topic" : transcriptView.kind === "topics" ? "Topic" : "Source turn · no AI grouping"}</span>
                              {group.topic && activeRecording && <button className="small-button" disabled={Boolean(busy)} onClick={() => openTopicEditor(activeRecording, group.topic!)}>Edit topic</button>}
                            </div>
                            <div className="topic-speakers">
                              <Users size={13} aria-hidden="true" />
                              {group.speakerLabels.map((label) => label === "unknown" ? "Speaker unknown" : anonymousSpeakerLabel(label)).join(" · ")}
                            </div>
                            {group.mixedSpeakers && <p className="topic-warning">Multiple speakers in this topic. Selecting the whole topic includes different voices; select only your own fragments below.</p>}
                            {group.hasUnknownSpeaker && <p className="topic-warning">Some speech has an unknown or overlapping speaker. Review the audio and leave uncertain fragments unselected.</p>}
                            {pendingIds.length > 0 && <div className="topic-selection">
                              <SelectionCheckbox label={transcriptView.kind === "topics" ? "Select topic" : "Select turn"}
                                ariaLabel={`Select all pending fragments in ${transcriptView.kind === "topics" ? "topic" : "source turn"} ${groupIndex + 1}: ${group.title}`}
                                checked={count === pendingIds.length} indeterminate={count > 0 && count < pendingIds.length}
                                disabled={transcriptBusy}
                                onChange={(checked) => setSelectedSegments((previous) => toggleIds(previous, pendingIds, checked, selectableSegmentIds))} />
                              <span>{count}/{pendingIds.length} pending selected</span>
                            </div>}
                            {group.summary && <div className="topic-summary">
                              <strong>Topic summary</strong>
                              <p>{group.summary}</p>
                            </div>}
                            <div className="topic-original">
                              <strong>Original words</strong>
                              {group.segments.map((segment, index) => <p key={`${getSegmentId(segment) || "source"}-${index}`} lang="en">{segment.text}</p>)}
                            </div>
                            <details className="topic-fragments">
                              <summary>Review {group.segments.length} original fragment{group.segments.length === 1 ? "" : "s"} · playback, speakers & selection</summary>
                              <div className="transcript-list">
                                {group.segments.map((segment, index) => {
                                  const id = getSegmentId(segment);
                                  const confirmed = isIdentityConfirmed(segment);
                                  const mine = segment.subject_id === "user" || segment.speaker === "user";
                                  return (
                                    <div className="transcript-segment" key={`${id || "source"}-${index}`}>
                                      <input type="checkbox" aria-label={`Select fragment at ${timeLabel(segment.start)}: ${segment.text}`}
                                        checked={Boolean(id && effectiveSelection.includes(id))}
                                        disabled={transcriptBusy || confirmed || !id}
                                        onChange={(event) => id && setSelectedSegments((previous) => toggleIds(previous, [id], event.target.checked, selectableSegmentIds))} />
                                      <div>
                                        <div className="segment-meta">
                                          <button className="time-button" disabled={Boolean(busy)}
                                            onClick={() => {
                                              if (audioRef.current) {
                                                audioRef.current.currentTime = segment.start;
                                                void audioRef.current.play().catch(() => setSourceError("Use the recording player's play button to start playback."));
                                              }
                                            }} aria-label={`Play from ${timeLabel(segment.start)}`}>
                                            <Play size={11} /> {timeLabel(segment.start)} — {timeLabel(segment.end)}
                                          </button>
                                          <span>{anonymousSpeakerLabel(segment.speaker_label) && `${anonymousSpeakerLabel(segment.speaker_label)} · `}
                                            {confirmed ? mine ? "Confirmed as yours" : "Identity already confirmed" : !id ? "Source ID unavailable" : anonymousSpeakerLabel(segment.speaker_label) ? "Identity unconfirmed" : "Speaker unknown"}</span>
                                        </div>
                                        <p lang="en">{segment.text}</p>
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            </details>
                          </article>
                        );
                      })}
                    </div>
                    {selectableSegmentIds.length > 0 && (
                      <>
                      <button
                        className="button primary full-width"
                        disabled={!effectiveSelection.length || transcriptBusy}
                        aria-describedby="transcript-selection-help"
                        onClick={() => void confirmSegments()}
                      >
                        <CheckCircle2 size={17} />
                        Confirm {effectiveSelection.length} segment{effectiveSelection.length === 1 ? "" : "s"} as mine
                      </button>
                      <p className="source-help confirmation-help">This confirms who spoke. It does not change your support preferences or the original source words.</p>
                      </>
                    )}
                  </section>
                )}
                {source && (
                  <section className="source-section">
                    <h3 className="small-heading">Memory details and versions</h3>
                    <div className="metadata-grid">
                      <span>
                        Speaker
                        <strong>
                          {source.memory.subject_id === "user"
                            ? "You (confirmed)"
                            : "Unknown"}
                        </strong>
                      </span>
                      <span>
                        Scope
                        <strong>
                          {scopeLabels[source.memory.scope] ||
                            (source.memory.scope ? "Recording context" : "Unspecified")}
                        </strong>
                      </span>
                      <span>
                        Uploading date
                        <strong>{uploadingDateLabel(activeRecording)}</strong>
                      </span>
                      <span>
                        Status
                        <strong>
                          {statusNames[source.memory.status] ||
                            source.memory.status}
                        </strong>
                      </span>
                    </div>
                    {source.versions?.length > 0 && (
                      <div className="version-list">
                        {source.versions.map((version, index) => (
                          <div key={`${version.version}-${index}`}>
                            <span>v{version.version}</span>
                            <p>{version.content}</p>
                            <small>
                              {version.version === source.memory.version
                                ? "Current version"
                                : "Superseded"}
                            </small>
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                )}
                {!source && activeRecording && (
                  <button
                    className="text-button danger-text"
                    onClick={() => deleteRecording(activeRecording)}
                  >
                    <Trash2 size={15} />
                    Delete the whole recording and related memories
                  </button>
                )}
              </>
            )}
          </div>
          <div className="drawer-foot">
            <button className="text-button" onClick={closeDrawer}>
              <ArrowLeft size={15} />
              Back to {viewNames[view]}
            </button>
            <span>Keeps Uploading date and page</span>
          </div>
        </Modal>
      )}
      {danger && (
        <Modal
          title={danger.title}
          close={closeDanger}
          className="danger-modal"
        >
          <div className="modal-body">
            {error && (
              <div className="alert alert-error" role="alert">
                {error}
              </div>
            )}
            <div className="danger-icon">
              <Trash2 size={26} />
            </div>
            <p>{danger.description}</p>
            <div className="modal-actions">
              <button className="button secondary" onClick={closeDanger}>
                Cancel
              </button>
              <button
                className="button danger-button"
                disabled={Boolean(busy)}
                onClick={() =>
                  void run(
                    "delete",
                    async () => {
                      await danger.run();
                      setDanger(null);
                    },
                    "Deleted; related understanding updated.",
                  )
                }
              >
                {busy === "delete" ? "Deleting…" : "Delete permanently"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function Preferences({
  settings,
  health,
  busy,
  close,
  save,
  error,
}: {
  error: string;
  settings: Settings;
  health: Health | null;
  busy: boolean;
  close: () => void;
  save: (patch: Partial<Settings>) => void;
}) {
  const status = health?.asr || {};
  const externalAsr = Boolean(status.external);
  const hosted = health?.deployment?.mode === "server";
  const pipeline = health?.memory_pipeline;
  return (
    <Modal title="Preferences & model" close={close}>
      <div className="modal-body">
        {error && (
          <div className="alert alert-error" role="alert">
            {error}
          </div>
        )}
        <p className="modal-description">
          Choose the support you would like to receive. Changes apply to pending support.
        </p>
        <div className="preference-list">
          {(["reminder", "suggestion", "comfort", "warning"] as CareType[]).map(
            (type) => (
              <label key={type} className="preference-row">
                <span>
                  <strong>{careLabels[type]}</strong>
                  <small>
                    {type === "reminder"
                      ? "Follow up only on items you allowed reminders for"
                      : type === "suggestion"
                        ? "Offer a small workable step when a need is clear"
                        : type === "comfort"
                          ? "Offer a little support when you say you need it"
                          : "Offer a supportive check-in when your confirmed words show strong distress or repeated emotional or social strain"}
                  </small>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  aria-label={careLabels[type]}
                  disabled={busy}
                  checked={supportPreferenceEnabled(settings, type)}
                  onChange={(event) =>
                    save(supportPreferencePatch(settings, type, event.target.checked))
                  }
                />
              </label>
            ),
          )}
        </div>
        <label className="preference-row">
          <span>
            <strong>Quiet mode</strong>
            <small>Pause automatic proactive support</small>
          </span>
          <input
            type="checkbox"
            role="switch"
            disabled={busy}
            checked={Boolean(settings.quiet_mode)}
            onChange={(event) => save({ quiet_mode: event.target.checked })}
          />
        </label>
        <div className="preferences-fields">
          <label>
            Minimum interval between automatic support cards (minutes)
            <input
              type="number"
              min="0"
              max="1440"
              disabled={busy}
              value={settings.frequency_minutes ?? 30}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (value >= 0 && value <= 1440)
                  save({ frequency_minutes: value });
              }}
            />
          </label>
          <label>
            Retention period (days)
            <input
              type="number"
              min="1"
              max="3650"
              disabled={busy}
              value={settings.retention_days ?? 30}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (value >= 1 && value <= 3650)
                  save({ retention_days: value });
              }}
            />
          </label>
        </div>
        <div className="model-panel">
          <div className="note-line">
            <AudioLines size={17} />
            <strong>Transcription service</strong>
            <span className="tag">{externalAsr ? "Cloud API" : hosted ? "This server" : "This device"}</span>
          </div>
          <p>
            {transcriptionProviderLabel(status.provider)}
            {" · "}{String(status.model || health?.model || "Configured by the backend")}
          </p>
          <dl>
            <div>
              <dt>Status</dt>
              <dd>
                {transcriptionStageNames[String(status.state || "")] || (status.loaded
                  ? externalAsr ? "Credentials configured" : "Loaded"
                  : status.available
                    ? externalAsr ? "Credentials configured" : ["qwen", "sensevoice"].includes(String(status.provider)) ? "Loads on demand" : "Loads on first use"
                    : String(status.status || status.state || "Waiting for setup"))}
              </dd>
            </div>
            <div>
              <dt>Processing</dt>
              <dd>{externalAsr ? "External cloud service" : `${hosted ? "This demo server" : "This device"} · ${String(status.device || "Backend-selected hardware")}`}</dd>
            </div>
            <div>
              <dt>Speaker separation</dt>
              <dd>{status.speaker_diarization ? status.available ? "Anonymous labels; identity needs confirmation" : "Configured; waiting for runtime and models" : "Not enabled; confirm each segment yourself"}</dd>
            </div>
            {["qwen", "sensevoice"].includes(String(status.provider)) && (
              <div>
                <dt>{status.provider === "sensevoice" ? "Segment timing" : "Word timing"}</dt>
                <dd>{status.provider === "sensevoice" ? "Speech/speaker estimates; no word timestamps" : "Estimated by Qwen3-ForcedAligner"}</dd>
              </div>
            )}
          </dl>
          {typeof status.error === "string" && status.error && (
            <p className="error-text">{englishMessage(status.error)}</p>
          )}
          <small>
            {externalAsr
              ? "Audio is sent to the transcription provider. Configured credentials do not confirm a successful live request."
              : ["qwen", "sensevoice"].includes(String(status.provider))
                ? "Install the runtime and download the speech and speaker models first. Timing and speaker labels are estimates; overlapping or uncertain speech stays unknown. You confirm speaker identity separately."
                : "The model may need to download on first use. You confirm who was speaking."}
          </small>
        </div>
        <div className="model-panel">
          <div className="note-line">
            <Layers3 size={17} />
            <strong>Memory understanding</strong>
            <span className="tag">{pipeline?.mode === "deepseek" ? "DeepSeek" : "Local rules"}</span>
          </div>
          <dl>
            <div>
              <dt>Status</dt>
              <dd>{pipeline?.mode === "deepseek" ? pipeline.configured ? "Credentials configured" : "Waiting for DeepSeek setup" : "Local rule-based demo"}</dd>
            </div>
            {pipeline?.mode === "deepseek" && (
              <div>
                <dt>Model</dt>
                <dd>{pipeline.model || "Configured by the backend"}</dd>
              </div>
            )}
          </dl>
          <small>
            {pipeline?.mode === "deepseek"
              ? `Atomic memories, consolidation, context and governance use DeepSeek. ${memoryTransferNotice(pipeline.provider)} Live access must be verified by a completed request.`
              : "Uses local rules to prepare tentative understanding."}
            {" "}Speaker identity is confirmed separately. Topic edits do not grant permissions or change support preferences.
          </small>
        </div>
        <DatasetPanel />
      </div>
    </Modal>
  );
}
