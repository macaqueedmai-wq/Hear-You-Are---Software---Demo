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
  MoreHorizontal,
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
  WandSparkles,
  X,
} from "lucide-react";
import { SidebarItem } from "./components/SidebarItem";
import { DatasetPanel } from "./components/DatasetPanel";
import { api, ApiError } from "./api";
import type {
  Card,
  CareType,
  Category,
  Health,
  Memory,
  Recording,
  Settings,
  Source,
  State,
} from "./types";

type View = "daily" | "import" | "memory" | "care";
type Danger = { title: string; description: string; run: () => Promise<void> };
const categoryLabels: Record<Category, string> = {
  mood: "心情與感受",
  social: "社交與連結",
  moments: "重要片刻",
};
const careLabels: Record<CareType, string> = {
  reminder: "提醒",
  suggestion: "建議",
  comfort: "安慰",
  warning: "預警",
};
const categoryIcons = { mood: Heart, social: Users, moments: Sprout };
const viewNames: Record<View, string> = {
  daily: "今日卡片",
  import: "錄音匯入",
  memory: "記憶與來源",
  care: "主動式 AI",
};
const statusNames: Record<string, string> = {
  queued: "等候處理",
  processing: "正在轉寫",
  transcribing: "正在轉寫",
  ready: "轉寫完成",
  completed: "轉寫完成",
  pending: "等待確認",
  failed: "處理失敗",
  error: "處理失敗",
  confirmed: "已確認",
  active: "有效",
  deleted: "已刪除",
  superseded: "已更新",
  uploaded: "已匯入",
};
const scopeLabels: Record<string, string> = {
  recorded_conversation: "該段對話",
  stated_preference: "明確表達的偏好",
  demo: "虛構演示",
  current_context: "目前情境",
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
  return new Intl.DateTimeFormat("zh-Hant-HK", {
    month: "long",
    day: "numeric",
    weekday: "long",
    timeZone: "Asia/Hong_Kong",
  }).format(new Date(`${date}T12:00:00+08:00`));
}
function shortDate(date?: string | null) {
  return date
    ? new Intl.DateTimeFormat("zh-Hant-HK", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Hong_Kong",
      }).format(new Date(date))
    : "錄音時間未提供";
}
function errorText(error: unknown) {
  return error instanceof Error ? error.message : "操作未完成，請稍後重試。";
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
          <button className="icon-button" onClick={close} aria-label="關閉視窗">
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
function DailyCard({
  card,
  open,
  confirm,
  busy,
}: {
  card: Card;
  open: () => void;
  confirm: () => void;
  busy: boolean;
}) {
  const Icon = categoryIcons[card.category] || Sprout;
  return (
    <article className={`daily-card category-${card.category}`}>
      <div className="card-topline">
        <span className="category-icon">
          <Icon size={22} />
        </span>
        <span className="eyebrow">{categoryLabels[card.category]}</span>
        <span className="card-more" aria-hidden="true">
          <MoreHorizontal size={21} />
        </span>
      </div>
      <h3>{card.title}</h3>
      <p className="card-summary">{card.summary}</p>
      <div className="card-tags">
        {card.analysis_mode === "llm" && <span className="tag">模型整理</span>}
        {card.is_demo && <span className="tag tag-demo">含虛構演示</span>}
        <span className="tag">
          {card.review_status === "confirmed" ? "理解已核實" : "理解待核實"}
        </span>
      </div>
      {card.review_status !== "confirmed" && (
        <div className="review-question">
          <MessageCircle size={16} />
          <p>
            {card.review_prompt ||
              "這些事情有發生嗎？我有理解對你嗎？需要怎樣修改？"}
          </p>
        </div>
      )}
      <div className="review-actions">
        <button
          className="small-button"
          onClick={confirm}
          disabled={
            busy || card.review_status === "confirmed" || !card.review_token
          }
        >
          <Check size={14} />
          {card.review_status === "confirmed" ? "理解已核實" : "理解正確"}
        </button>
        <button className="small-button ghost" onClick={open}>
          需要修改
        </button>
      </div>
      <div className="card-footer">
        <span>
          <AudioLines size={14} />
          {card.memory_ids?.length || 0} 則記憶依據
        </span>
        <button className="text-button" onClick={open}>
          查看來源 <ArrowRight size={15} />
        </button>
      </div>
    </article>
  );
}

function CareCard({
  card,
  feedback,
  open,
  memories,
  openMemory,
  schedule,
  busy,
}: {
  card: Card;
  feedback: (id: string, action: string) => void;
  open: () => void;
  memories: Memory[];
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
    sleep_pattern: "跨日睡眠記錄",
    social_follow_up: "社交狀況跟進",
    emotional_follow_up: "情緒狀況跟進",
    current_request: "當前明確需要",
  };
  async function scheduleBedtime(event: FormEvent) {
    event.preventDefault();
    setScheduleError("");
    if (!bedtime || new Date(`${bedtime}:00+08:00`).getTime() <= Date.now()) {
      setScheduleError("請選擇未來的香港時間。");
      return;
    }
    if (await schedule(card, `${bedtime}:00+08:00`)) {
      setShowSchedule(false);
      setBedtime("");
    } else
      setScheduleError(
        "提醒未能設定。請查看上方訊息，重新檢查來源或再試一次。",
      );
  }
  return (
    <article className={`care-card care-${type}`}>
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
          <span className="eyebrow">
            {careLabels[type]}
            {card.is_demo ? " · 含虛構演示" : ""}
          </span>
          <h3>{card.title}</h3>
        </div>
      </div>
      <p>{card.summary || card.action_text}</p>
      <div className="condition">
        <ShieldCheck size={16} />
        <span>
          <strong>觸發依據</strong>
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
              <h4>過往記錄</h4>
              {historical.map((memory) => (
                <button
                  className="support-source"
                  key={memory.id}
                  onClick={() => openMemory(memory.id)}
                >
                  <span>
                    {shortDate(memory.event_time)}
                    {memory.is_demo && <i>虛構演示</i>}
                  </span>
                  <strong>{memory.content}</strong>
                  <ArrowRight size={13} />
                </button>
              ))}
            </div>
          )}
          {current.length > 0 && (
            <div>
              <h4>當前記錄</h4>
              {current.map((memory) => (
                <button
                  className="support-source"
                  key={memory.id}
                  onClick={() => openMemory(memory.id)}
                >
                  <span>
                    {shortDate(memory.event_time)}
                    {memory.is_demo && <i>虛構演示</i>}
                  </span>
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
              設定睡覺提醒
            </button>
          ) : (
            <form onSubmit={(event) => void scheduleBedtime(event)}>
              <label>
                你想何時收到睡覺提醒？（香港時間）
                <input
                  aria-label="睡覺提醒時間"
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
                  允許並設定提醒
                </button>
                <button
                  className="text-button"
                  type="button"
                  onClick={() => setShowSchedule(false)}
                >
                  取消
                </button>
              </div>
            </form>
          )}
          <p>只有你明確設定後才建立提醒；應用開啟時檢查，沒有系統背景鬧鐘。</p>
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
          {type === "reminder" ? "已完成" : "對我有幫助"}
        </button>
        <button
          className="small-button ghost"
          disabled={busy}
          onClick={() => feedback(card.id, "snooze")}
        >
          <Clock3 size={14} />
          稍後再看
        </button>
        <button className="text-button" onClick={open}>
          來源 <ArrowRight size={14} />
        </button>
        <button
          className="icon-button"
          aria-label="略過這張關懷卡片"
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
  const [showDemo, setShowDemo] = useState(false);
  const [danger, setDanger] = useState<Danger | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [drawerRecording, setDrawerRecording] = useState<Recording | null>(
    null,
  );
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState("");
  const [edit, setEdit] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [allowFollowup, setAllowFollowup] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [selectedSegments, setSelectedSegments] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [file, setFile] = useState<File | null>(null);
  const [recordedAt, setRecordedAt] = useState("");
  const [dragging, setDragging] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const selectedDate = date || state?.date || localDate();
  const closeDrawer = useCallback(() => {
    setSource(null);
    setDrawerRecording(null);
    setSourceError("");
    setConflict(false);
  }, []);
  const closeSettings = useCallback(() => setShowSettings(false), []);
  const closeDemo = useCallback(() => setShowDemo(false), []);
  const closeDanger = useCallback(() => setDanger(null), []);

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
    const timer = window.setInterval(() => void refresh(date, true), 5000);
    return () => window.clearInterval(timer);
  }, [refresh, date]);
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
    setConflict(false);
    setSource(null);
    setDrawerRecording(null);
    try {
      const next = await api<Source>(`/memories/${id}/source`);
      setSource(next);
      setEdit(next.memory.content);
      setAllowFollowup(Boolean(next.memory.follow_up?.allowed));
      setDueAt(next.memory.follow_up?.due_at?.slice(0, 16) || "");
      setSelectedSegments([]);
    } catch (err) {
      setSourceError(errorText(err));
    } finally {
      setSourceLoading(false);
    }
  }
  function openRecording(recording: Recording) {
    setSource(null);
    setDrawerRecording(recording);
    setSelectedSegments([]);
    setSourceError("");
    setConflict(false);
  }
  function openCard(card: Card) {
    if (card.memory_ids?.[0]) void openMemory(card.memory_ids[0]);
    else setNotice("這張卡片目前沒有可查看的有效來源。");
  }
  async function confirmCard(card: Card) {
    if (!card.review_token) {
      setError("卡片需要重新整理後才能核實。");
      return;
    }
    await run(
      `review-${card.id}`,
      () =>
        api(`/cards/${card.id}/confirm`, {
          method: "POST",
          body: JSON.stringify({
            expected_versions: card.versions,
            review_token: card.review_token,
          }),
        }),
      "已核實這張卡片的理解。主動支持會依據已核實記憶重新評估。",
    );
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
      "已按你的選擇設定睡覺提醒。到期時，應用需保持開啟才能檢查。",
    );
  async function saveMemory(event: FormEvent) {
    event.preventDefault();
    if (!source || !edit.trim()) return;
    if (allowFollowup && !dueAt) {
      setSourceError("請先提供跟進時間，或取消允許跟進。");
      return;
    }
    setBusy("save-memory");
    setSourceError("");
    try {
      await api(`/memories/${source.memory.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          content: edit.trim(),
          version: source.memory.version,
          confirmed: true,
          follow_up:
            allowFollowup && dueAt
              ? {
                  due_at: `${dueAt}:00+08:00`,
                  allowed: true,
                  completed: source.memory.follow_up?.completed || false,
                }
              : {
                  due_at: source.memory.follow_up?.due_at || null,
                  allowed: false,
                  completed: source.memory.follow_up?.completed || false,
                },
        }),
      });
      await refresh(date, true);
      await openMemory(source.memory.id);
      setNotice("記憶已更新；相關卡片已重算，舊提示已取消。");
    } catch (err) {
      setConflict(err instanceof ApiError && err.status === 409);
      setSourceError(errorText(err));
    } finally {
      setBusy("");
    }
  }
  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    if (file.size > 100 * 1024 * 1024) {
      setError("檔案超過 100 MB。請先剪輯或壓縮，再匯入。");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    body.append("language", "en");
    if (recordedAt) body.append("recorded_at", `${recordedAt}:00+08:00`);
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
          ? "這個錄音已匯入，已保留原有來源，沒有重複增加證據。"
          : "錄音已匯入。轉寫完成後，請確認哪些片段是你說的。",
      );
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }
  async function confirmSegments() {
    const recording = drawerRecording || source?.recording;
    if (!recording || selectedSegments.length === 0) return;
    setBusy("confirm");
    setSourceError("");
    try {
      await api(`/recordings/${recording.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ segment_ids: selectedSegments }),
      });
      await refresh(date, true);
      setNotice("已確認所選片段屬於你，記憶與卡片已更新。");
      closeDrawer();
    } catch (err) {
      setSourceError(errorText(err));
    } finally {
      setBusy("");
    }
  }
  function deleteMemory(memory: Memory) {
    setDanger({
      title: "刪除這則記憶？",
      description:
        "此記憶及歷史版本的內容會清除，相關卡片及提示亦會更新。原始錄音仍保留；如需移除整段錄音，請到錄音匯入頁。",
      run: async () => {
        await api(`/memories/${memory.id}`, { method: "DELETE" });
        closeDrawer();
      },
    });
  }
  function deleteRecording(recording: Recording) {
    setDanger({
      title: "刪除這段錄音及相關記憶？",
      description:
        "此操作會移除本機錄音檔、逐字稿及關聯記憶和歷史，並更新相關卡片。刪除後無法復原。",
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
  const feedback = (id: string, action: string) =>
    void run(
      `feedback-${id}`,
      () =>
        api(`/proactive/${id}/feedback`, {
          method: "POST",
          body: JSON.stringify({ action }),
        }),
      action === "done"
        ? "事項已標示完成。"
        : action === "snooze"
          ? "已稍後再看。此操作只更改接收狀態。"
          : "已記錄你的回饋。",
    );
  const memories = (state?.memories || []).filter(
    (memory) => memory.status !== "deleted" && memory.status !== "superseded",
  );
  const filteredMemories = memories.filter(
    (memory) =>
      (filter === "all" ||
        memory.category === filter ||
        (filter === "review_pending" &&
          !memory.reviewed &&
          memory.subject_id === "user") ||
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
        (segment) =>
          segment.subject_id !== "user" && segment.speaker !== "user",
      ),
  );
  const activeMemories = memories.filter(
    (memory) => memory.status === "active",
  );
  const drawerVisible = Boolean(
    source || drawerRecording || sourceLoading || sourceError,
  );
  const activeRecording = drawerRecording || source?.recording;
  const segments = source?.segments || drawerRecording?.segments || [];
  const requiresSubjectConfirmation = Boolean(
    source &&
      (source.memory.subject_id !== "user" ||
        segments.some(
          (segment) =>
            segment.subject_id !== "user" && segment.speaker !== "user",
        )),
  );
  const asr = health?.asr;
  const modelStatus = asr?.loaded
    ? "模型已載入"
    : asr?.available
      ? "首次使用時載入"
      : health
        ? "等待設定模型"
        : "連線中";
  const currentCards = state?.cards || [];
  const careCards = state?.proactive || [];
  const selectedCareType = state?.assessment?.matched_types || [];

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
          aria-label="Hear You Are 首頁"
        >
          <span className="brand-mark">
            <Ear size={27} />
            <span />
          </span>
          <span>
            Hear You Are<small>讓每一天，都被聽見</small>
          </span>
        </a>
        <div className="workspace-label">你的日常空間</div>
        <nav aria-label="主要導覽">
          <SidebarItem
            icon={<Layers3 size={19} />}
            label="今日卡片"
            isActive={view === "daily"}
            onClick={() => setView("daily")}
          />
          <SidebarItem
            icon={<AudioLines size={19} />}
            label="錄音匯入"
            isActive={view === "import"}
            onClick={() => setView("import")}
          />
          <SidebarItem
            icon={<Database size={19} />}
            label="記憶與來源"
            isActive={view === "memory"}
            onClick={() => setView("memory")}
          />
          <SidebarItem
            icon={<Sparkles size={19} />}
            label="主動式 AI"
            isActive={view === "care"}
            onClick={() => setView("care")}
          />
        </nav>
        <div className="sidebar-note">
          <div className="note-line">
            <LockKeyhole size={16} />
            <strong>你的記憶，由你掌握</strong>
          </div>
          <p>隨時查看原話、修正理解，或刪除不想保留的內容。</p>
        </div>
        <div className="sidebar-bottom">
          <button
            className="settings-nav"
            onClick={() => setShowSettings(true)}
          >
            <Settings2 size={18} />
            偏好與模型
            <span className="status-dot" />
          </button>
          <div className="profile">
            <span className="avatar">你</span>
            <div>
              <strong>我的日常</strong>
              <small>本機軟件 · MVP</small>
            </div>
            <span className="profile-dot" />
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            我的空間<span>/</span>
            <strong>{viewNames[view]}</strong>
          </div>
          <div className="topbar-actions">
            <span className="local-badge">
              <span
                className={`status-dot ${error && !state ? "offline" : ""}`}
              />
              {error && !state ? "服務未連線" : "本機運行"}
            </span>
            <button
              className="icon-button"
              aria-label="開啟偏好設定"
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
                  ? "把零散片刻，整理成理解"
                  : view === "import"
                    ? "從一段錄音開始"
                    : view === "memory"
                      ? "每一份理解，都有來源"
                      : "在合適的時候，給一點支持"}
              </div>
              <h1>
                {viewNames[view]}
                <span className="heading-sparkle">
                  {view === "daily" ? <Sparkles size={24} /> : null}
                </span>
              </h1>
              <p>
                {view === "daily"
                  ? "回看心情、連結與值得記住的事。你可以確認，也可以修正。"
                  : view === "import"
                    ? "上傳原有錄音，完成英文轉寫，再確認屬於你的片段。"
                    : view === "memory"
                      ? "查看記憶的原話和版本，讓它更貼近你的實際經歷。"
                      : "只有符合前置條件，才會出現提醒、建議、安慰或預警。"}
              </p>
            </div>
            <div className="heading-actions">
              {view === "daily" && (
                <button
                  className="button secondary"
                  disabled={
                    !health?.analysis?.configured ||
                    Boolean(busy) ||
                    !currentCards.length
                  }
                  title={
                    health?.analysis?.configured
                      ? `使用 ${health.analysis.model || "已設定模型"} 整理目前卡片`
                      : "可在 .env 設定本機或相容模型；目前為本機規則示例"
                  }
                  onClick={() =>
                    void run(
                      "generate",
                      () =>
                        api("/analysis/generate", {
                          method: "POST",
                          body: JSON.stringify({ date: selectedDate }),
                        }),
                      "模型已整理目前片段，來源仍可追溯與修正。",
                    )
                  }
                >
                  {busy === "generate" ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Sparkles size={16} />
                  )}
                  AI 整理
                </button>
              )}
              <button
                className="button secondary"
                onClick={() => setShowDemo(true)}
              >
                <WandSparkles size={16} />
                體驗演示
              </button>
              {view !== "import" && (
                <button
                  className="button primary"
                  onClick={() => setView("import")}
                >
                  <Plus size={18} />
                  匯入錄音
                </button>
              )}
            </div>
          </div>
          {error && (
            <div className="alert alert-error" role="alert">
              <span>{error}</span>
              <button className="text-button" onClick={() => void refresh()}>
                重試 <RefreshCw size={15} />
              </button>
              <button
                className="icon-button"
                aria-label="關閉錯誤訊息"
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
                aria-label="關閉通知"
                onClick={() => setNotice("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {loading && !state ? (
            <div className="loading-state" role="status">
              <LoaderCircle className="spin" size={30} />
              <p>正在整理你的空間…</p>
            </div>
          ) : (
            <>
              {(view === "daily" || view === "care") && (
                <div className="date-row">
                  <div className="date-selector">
                    <button
                      className="icon-button"
                      aria-label="前一日"
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
                        aria-label="選擇卡片日期"
                        type="date"
                        value={selectedDate}
                        onChange={(event) => setDate(event.target.value)}
                      />
                    </label>
                    <button
                      className="icon-button"
                      aria-label="後一日"
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
                    {niceDate(selectedDate)}
                    {selectedDate === localDate() && (
                      <span className="tag">今天</span>
                    )}
                  </span>
                  <button
                    className="refresh-button"
                    disabled={Boolean(busy)}
                    onClick={() => void refresh(date, true)}
                  >
                    <RefreshCw size={14} />
                    重新整理
                  </button>
                </div>
              )}
              {view === "daily" && (
                <>
                  <section className="welcome-banner">
                    <div className="banner-copy">
                      <span className="banner-label">
                        <Sprout size={15} />
                        給自己一點空間
                      </span>
                      <h2>你的一天，值得好好聽見。</h2>
                      <p>
                        {currentCards.length
                          ? "這些片刻來自你確認的本人片段。慢慢看，請核實每張卡片是否理解正確。"
                          : "不必記住每一句話。匯入錄音，讓重要的心情與連結慢慢浮現。"}
                      </p>
                      <div className="banner-status">
                        <span>
                          <AudioLines size={14} />
                          {recordings.length} 段錄音
                        </span>
                        <span>
                          <CheckCircle2 size={14} />
                          {activeMemories.length} 則有效記憶
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
                      <div className="art-caption">留意 · 理解 · 成長</div>
                    </div>
                  </section>
                  {pending.length > 0 && (
                    <div className="pending-banner">
                      <MessageCircle size={19} />
                      <div>
                        <strong>{pending.length} 段錄音等待你確認</strong>
                        <span>
                          轉寫已完成。確認哪些片段是你說的，才會用來整理卡片。
                        </span>
                      </div>
                      <button
                        className="text-button"
                        onClick={() => openRecording(pending[0])}
                      >
                        查看片段 <ArrowRight size={16} />
                      </button>
                    </div>
                  )}
                  {Number(state?.coverage?.undated_memories || 0) > 0 && (
                    <div className="pending-banner">
                      <CalendarDays size={18} />
                      <div>
                        <strong>
                          {String(state?.coverage?.undated_memories)}{" "}
                          則記憶沒有錄音發生時間
                        </strong>
                        <span>未歸入特定日期，仍可在記憶與來源頁查看。</span>
                      </div>
                      <button
                        className="text-button"
                        onClick={() => setView("memory")}
                      >
                        查看記憶 <ArrowRight size={15} />
                      </button>
                    </div>
                  )}
                  <section>
                    <div className="section-heading">
                      <h2>
                        每日片刻
                        <span className="section-count">
                          {currentCards.length.toString().padStart(2, "0")}
                        </span>
                      </h2>
                      <span>只涵蓋已匯入片段 · 可修正</span>
                    </div>
                    {currentCards.length ? (
                      <div className="daily-grid">
                        {currentCards.map((card) => (
                          <DailyCard
                            key={card.id}
                            card={card}
                            open={() => openCard(card)}
                            confirm={() => void confirmCard(card)}
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
                                    ? "留意你如何描述自己的感受"
                                    : category === "social"
                                      ? "記下日常中的對話與連結"
                                      : "保留對你重要的事與承諾"}
                                </p>
                                <div className="placeholder-lines">
                                  <i />
                                  <i />
                                </div>
                                <span className="placeholder-note">
                                  匯入並確認錄音後開始整理
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
                          此刻的關懷
                        </h2>
                        <button
                          className="text-button"
                          onClick={() => setView("care")}
                        >
                          查看全部 <ArrowRight size={15} />
                        </button>
                      </div>
                      {careCards.length ? (
                        <CareCard
                          card={careCards[0]}
                          feedback={feedback}
                          open={() => openCard(careCards[0])}
                          memories={memories}
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
                            <h3>暫時沒有需要打擾你的事</h3>
                            <p>
                              {state?.assessment?.reason ||
                                "條件符合時，關懷才會出現在這裏。"}
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="how-card">
                      <div className="section-heading">
                        <h2>從錄音，到理解</h2>
                        <Layers3 size={18} />
                      </div>
                      <div className="mini-flow">
                        <span>
                          <FileAudio size={17} />
                          <small>原話</small>
                        </span>
                        <ArrowRight size={14} />
                        <span>
                          <Database size={17} />
                          <small>記憶</small>
                        </span>
                        <ArrowRight size={14} />
                        <span>
                          <Heart size={17} />
                          <small>卡片</small>
                        </span>
                      </div>
                      <p>每張卡片都能查看來源。修正一處，相關理解亦會更新。</p>
                      <button
                        className="text-button"
                        onClick={() => setView("memory")}
                      >
                        管理我的記憶 <ArrowRight size={15} />
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
                      <h2>{file ? file.name : "把錄音帶進你的空間"}</h2>
                      <p>
                        {file
                          ? `${(file.size / 1024 / 1024).toFixed(1)} MB · 準備匯入`
                          : "拖放檔案到這裏，或選擇電腦中的錄音"}
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
                        {file ? "重新選擇" : "選擇錄音檔"}
                      </button>
                      <small>
                        MP3、WAV、M4A、FLAC、OGG、WebM · 最多 100 MB／20 分鐘
                      </small>
                    </div>
                    <div className="upload-options">
                      <label>
                        錄音發生時間 <span>選填 · 香港時間</span>
                        <input
                          type="datetime-local"
                          value={recordedAt}
                          onChange={(event) =>
                            setRecordedAt(event.target.value)
                          }
                        />
                      </label>
                      <div>
                        <span className="field-label">轉錄語言</span>
                        <div className="language-field">
                          English <span className="tag">英文優先</span>
                        </div>
                        <small>
                          未提供時間時會標示「錄音時間未知」，不會以匯入時間代替。
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
                        {busy === "upload" ? "正在匯入…" : "匯入並轉寫"}
                      </button>
                    </div>
                  </form>
                  <div className="process-explainer">
                    {[
                      ["1", "本機英文轉寫", "Whisper large-v3-turbo"],
                      ["2", "確認你的片段", "保留未知說話者"],
                      ["3", "整理記憶與卡片", "每則理解保留來源"],
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
                        錄音紀錄
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
                              <p>
                                {shortDate(recording.recorded_at)}
                                {recording.is_demo && (
                                  <span className="tag tag-demo">
                                    虛構演示 · 沒有音訊檔
                                  </span>
                                )}
                              </p>
                              {recording.error && (
                                <p className="error-text">{recording.error}</p>
                              )}
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
                                      "已重新安排轉寫。",
                                    )
                                  }
                                >
                                  <RefreshCw size={14} />
                                  重試
                                </button>
                              )}
                              {Boolean(recording.segments?.length) && (
                                <button
                                  className="small-button"
                                  onClick={() => openRecording(recording)}
                                >
                                  查看逐字稿
                                </button>
                              )}
                              <button
                                className="icon-button"
                                aria-label={`刪除錄音 ${recording.filename}`}
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
                          title="這裏會保存你的錄音紀錄"
                        >
                          選擇一段錄音後，處理狀態與逐字稿會顯示在這裏。
                        </Empty>
                      </div>
                    )}
                  </section>
                </>
              )}
              {view === "memory" && (
                <>
                  <div className="memory-layers" aria-label="五層記憶架構">
                    {[
                      ["01", "原始證據", FileAudio],
                      ["02", "原子記憶", Database],
                      ["03", "記憶整合", Layers3],
                      ["04", "檢索上下文", Search],
                      ["05", "治理", ShieldCheck],
                    ].map(([n, label, Icon]) => {
                      const LayerIcon = Icon as typeof FileAudio;
                      return (
                        <div key={String(n)}>
                          <span>{String(n)}</span>
                          <LayerIcon size={19} />
                          <strong>{String(label)}</strong>
                        </div>
                      );
                    })}
                  </div>
                  <div className="memory-toolbar">
                    <label className="search-field">
                      <Search size={17} />
                      <input
                        placeholder="搜尋記憶內容或情境"
                        aria-label="搜尋記憶"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                      />
                      {query && (
                        <button
                          className="icon-button"
                          aria-label="清除搜尋"
                          onClick={() => setQuery("")}
                        >
                          <X size={15} />
                        </button>
                      )}
                    </label>
                    <select
                      aria-label="篩選記憶"
                      value={filter}
                      onChange={(event) => setFilter(event.target.value)}
                    >
                      <option value="all">全部記憶</option>
                      <option value="mood">心情與感受</option>
                      <option value="social">社交與連結</option>
                      <option value="moments">重要片刻</option>
                      <option value="pending">等待主體確認</option>
                      <option value="review_pending">理解待核實</option>
                    </select>
                    <span className="result-count">
                      {filteredMemories.length} 則記憶
                    </span>
                  </div>
                  {filteredMemories.length ? (
                    <div className="memory-list">
                      {filteredMemories.map((memory) => {
                        const Icon = categoryIcons[memory.category] || Database;
                        return (
                          <article className="memory-row" key={memory.id}>
                            <span
                              className={`category-icon category-${memory.category}`}
                            >
                              <Icon size={20} />
                            </span>
                            <div>
                              <div className="memory-row-meta">
                                <span>{categoryLabels[memory.category]}</span>
                                <span>v{memory.version}</span>
                                <span>
                                  {memory.confirmed
                                    ? "主體已確認"
                                    : "主體待確認"}
                                </span>
                                <span>
                                  {memory.reviewed
                                    ? "理解已核實"
                                    : "理解待核實"}
                                </span>
                                {memory.is_demo && (
                                  <span className="tag tag-demo">虛構演示</span>
                                )}
                              </div>
                              <h3>{memory.content}</h3>
                              <p>
                                {memory.event_time
                                  ? shortDate(memory.event_time)
                                  : "錄音時間未知"}{" "}
                                ·{" "}
                                {memory.subject_id === "user"
                                  ? "屬於你"
                                  : "說話者未知"}{" "}
                                ·{" "}
                                {memory.memory_type === "semantic"
                                  ? "偏好／語義記憶"
                                  : "事件記憶"}
                              </p>
                            </div>
                            <button
                              className="text-button"
                              onClick={() => void openMemory(memory.id)}
                            >
                              來源與修正 <ArrowRight size={16} />
                            </button>
                          </article>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="panel">
                      <Empty
                        icon={<Database size={27} />}
                        title={
                          query || filter !== "all"
                            ? "沒有符合的記憶"
                            : "記憶會在確認原話後開始累積"
                        }
                      >
                        {query || filter !== "all"
                          ? "試試其他關鍵字，或查看全部記憶。"
                          : "先匯入錄音，或載入有清楚標示的虛構演示。"}
                        {!query && filter === "all" && (
                          <>
                            <br />
                            原始證據、原子記憶、整合、檢索與治理，均可從這裏查看。
                          </>
                        )}
                      </Empty>
                    </div>
                  )}
                </>
              )}
              {view === "care" && (
                <>
                  <div className="section-heading ai-section-heading">
                    <h2>
                      <MessageCircle size={19} />
                      一、主動核實理解
                    </h2>
                    <span>本人片段不等於理解已核實</span>
                  </div>
                  <p className="ai-section-description">
                    我先整理，再請你核實。只有你確認的理解，才會用於主動支持。
                  </p>
                  {currentCards.some(
                    (card) => card.review_status !== "confirmed",
                  ) ? (
                    <div className="daily-grid review-grid">
                      {currentCards
                        .filter((card) => card.review_status !== "confirmed")
                        .map((card) => (
                          <DailyCard
                            key={card.id}
                            card={card}
                            open={() => openCard(card)}
                            confirm={() => void confirmCard(card)}
                            busy={Boolean(busy)}
                          />
                        ))}
                    </div>
                  ) : (
                    <div className="quiet-card review-empty">
                      <span>
                        <CheckCircle2 size={23} />
                      </span>
                      <div>
                        <h3>
                          {currentCards.length
                            ? "本日卡片的理解已核實"
                            : "暫時沒有待核實卡片"}
                        </h3>
                        <p>新摘要或來源改動後，會再次詢問你是否理解正確。</p>
                      </div>
                    </div>
                  )}
                  <div className="section-heading ai-section-heading">
                    <h2>
                      <Sparkles size={19} />
                      二、主動支持
                    </h2>
                    <span>已核實的歷史與當前記憶</span>
                  </div>
                  <div className="care-intro">
                    <span className="care-intro-icon">
                      <Heart size={26} />
                    </span>
                    <div>
                      <h2>支持有依據，也尊重你的節奏。</h2>
                      <p>
                        連結已核實的過往與當前記憶，依照偏好及前置條件提供支持。條件不明時，保持安靜。
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
                          "已重新檢查當前條件。",
                        )
                      }
                      disabled={Boolean(busy)}
                    >
                      {busy === "evaluate" ? (
                        <LoaderCircle className="spin" size={16} />
                      ) : (
                        <RefreshCw size={16} />
                      )}
                      檢查條件
                    </button>
                  </div>
                  <div className="condition-grid">
                    {(
                      [
                        "reminder",
                        "suggestion",
                        "comfort",
                        "warning",
                      ] as CareType[]
                    ).map((type) => (
                      <div
                        className={`condition-tile ${selectedCareType.includes(type) ? "matched" : ""}`}
                        key={type}
                      >
                        <div>
                          <strong>{careLabels[type]}</strong>
                          <span>
                            {selectedCareType.includes(type)
                              ? "符合條件"
                              : state?.settings?.preferences?.[type] === false
                                ? "已關閉"
                                : "未觸發"}
                          </span>
                        </div>
                        <p>
                          {type === "reminder"
                            ? "已核實、允許跟進，且到期未完成的事項。應用開啟時檢查。"
                            : type === "suggestion"
                              ? "清楚的需要、可行的行動，以及接收建議的意願。"
                              : type === "comfort"
                                ? "已確認的感受或支持需要，當下適合給予安慰。"
                                : "只用明確虛構情境示範；需在偏好中單獨啟用。"}
                        </p>
                      </div>
                    ))}
                  </div>
                  <div className="assessment">
                    <ShieldCheck size={18} />
                    <div>
                      <strong>本次條件檢查</strong>
                      <p>
                        {state?.assessment?.reason ||
                          "尚未有可評估的有效記憶。"}
                      </p>
                    </div>
                  </div>
                  {careCards.length ? (
                    <div className="care-grid">
                      {careCards.map((card) => (
                        <CareCard
                          key={card.id}
                          card={card}
                          feedback={feedback}
                          open={() => openCard(card)}
                          memories={memories}
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
                        title="此刻，讓你保留安靜的空間"
                      >
                        沒有符合條件的提示會保持不輸出。多類同時符合而選擇規則未定，也不會顯示卡片。
                      </Empty>
                    </div>
                  )}
                </>
              )}
              <footer className="page-footer">
                <span>
                  <ShieldCheck size={13} />
                  來源可追溯，理解可修正
                </span>
                <span>
                  分析模式：
                  {health?.analysis?.mode === "llm"
                    ? "已設定模型分析"
                    : "本機規則示例"}{" "}
                  · 不作臨床診斷
                </span>
                <a
                  href="https://github.com/kenlacroix/moodhaven-journal"
                  target="_blank"
                  rel="noreferrer"
                >
                  介面元件：MoodHaven · MIT
                </a>
              </footer>
            </>
          )}
        </div>
      </main>
      {showDemo && (
        <Modal title="體驗虛構演示" close={closeDemo}>
          <div className="modal-body">
            {error && (
              <div className="alert alert-error" role="alert">
                {error}
              </div>
            )}
            <p className="modal-description">
              使用固定英語範例，體驗卡片、來源修正及不同觸發條件。這些是虛構資料，沒有假裝存在的錄音檔。
            </p>
            <button
              className="button primary full-width"
              disabled={Boolean(busy)}
              onClick={() =>
                void run(
                  "demo-seed",
                  async () => {
                    await api("/demo/seed", { method: "POST" });
                    setDate("");
                    setView("daily");
                    setShowDemo(false);
                  },
                  "已載入虛構演示。你可以點卡片查看及修正來源。",
                )
              }
            >
              <WandSparkles size={17} />
              {busy === "demo-seed" ? "正在載入…" : "載入每日卡片演示"}
            </button>
            <div className="modal-divider" />
            <h3 className="small-heading">選擇主動式 AI 情境</h3>
            <p className="muted small-text">
              每次只切換演示資料，不會更改你的真實記憶。預警須先在偏好中啟用。
            </p>
            <div className="scenario-grid">
              {(
                [
                  "reminder",
                  "suggestion",
                  "comfort",
                  "warning",
                  "none",
                  "multiple",
                  "sleep",
                  "social_history",
                  "emotional_history",
                ] as const
              ).map((type) => (
                <button
                  className="scenario-button"
                  key={type}
                  disabled={Boolean(busy)}
                  onClick={() =>
                    void run(
                      `scenario-${type}`,
                      async () => {
                        await api("/demo/scenario", {
                          method: "POST",
                          body: JSON.stringify({ type }),
                        });
                        setDate("");
                        setView("care");
                        setShowDemo(false);
                      },
                      "已切換虛構演示情境。",
                    )
                  }
                >
                  <span>
                    {type === "none"
                      ? "零類符合"
                      : type === "multiple"
                        ? "多類符合"
                        : type === "sleep"
                          ? "跨日睡眠"
                          : type === "social_history"
                            ? "社交狀況跟進"
                            : type === "emotional_history"
                              ? "情緒狀況跟進"
                              : careLabels[type]}
                  </span>
                  <small>
                    {type === "none" || type === "multiple"
                      ? "驗證不輸出"
                      : type === "warning"
                        ? "需單獨啟用"
                        : type === "sleep"
                          ? "七天內三日記錄"
                          : type === "social_history" ||
                              type === "emotional_history"
                            ? "過往與今天的出處"
                            : "單類觸發演示"}
                  </small>
                  <ArrowRight size={15} />
                </button>
              ))}
            </div>
          </div>
        </Modal>
      )}
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
              "偏好已更新，待展示提示亦已重新檢查。",
            )
          }
        />
      )}
      {drawerVisible && (
        <Modal
          title={source ? "記憶與原話" : "錄音逐字稿"}
          close={closeDrawer}
          className="source-drawer"
        >
          <div className="drawer-content">
            {sourceLoading ? (
              <div className="loading-state">
                <LoaderCircle className="spin" size={24} />
                <p>正在讀取來源…</p>
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
                      <span>記憶 v{source.memory.version}</span>
                      {source.memory.is_demo && (
                        <span className="tag tag-demo">虛構演示</span>
                      )}
                    </div>
                    <h3>{source.memory.content}</h3>
                    <div className="evidence-label">
                      <ShieldCheck size={14} />
                      {source.memory.confirmed
                        ? "主體已確認"
                        : "等待主體確認"}{" "}
                      · {source.memory.reviewed ? "理解已核實" : "理解待核實"} ·{" "}
                      {source.memory.evidence_kind === "ai_inference"
                        ? "系統推測"
                        : source.memory.evidence_kind === "user_confirmed"
                          ? "用戶補充／修正"
                          : "原話表達"}
                    </div>
                  </div>
                )}
                {activeRecording && (
                  <section className="source-section">
                    <div className="source-section-title">
                      <h3>
                        <FileAudio size={17} />
                        原始錄音
                      </h3>
                      <span>{activeRecording.filename}</span>
                    </div>
                    {activeRecording.is_demo ? (
                      <div className="demo-audio">
                        <AudioLines size={22} />
                        <div>
                          <strong>虛構逐字稿</strong>
                          <p>此演示沒有音訊檔，故不提供錄音回放。</p>
                        </div>
                      </div>
                    ) : (
                      <audio
                        ref={audioRef}
                        controls
                        preload="metadata"
                        src={`/api/recordings/${activeRecording.id}/audio`}
                        onError={() =>
                          setSourceError(
                            "暫時未能讀取錄音。檔案可能已移除，請重新整理來源。",
                          )
                        }
                        aria-label="原始錄音播放"
                      />
                    )}
                  </section>
                )}
                {segments.length > 0 && (
                  <section className="source-section">
                    <div className="source-section-title">
                      <h3>
                        <MessageCircle size={17} />
                        逐字稿片段
                      </h3>
                      <span>原話保留英文</span>
                    </div>
                    {(!source || requiresSubjectConfirmation) && (
                      <p className="source-help">
                        請勾選確定是你說的片段。未確認的說話者不會自動當作你。
                      </p>
                    )}
                    <div className="transcript-list">
                      {segments.map((segment, index) => {
                        const id =
                          segment.id || segment.segment_id || String(index);
                        const confirmed =
                          segment.subject_id === "user" ||
                          segment.speaker === "user";
                        return (
                          <div className="transcript-segment" key={id}>
                            {(!source || requiresSubjectConfirmation) && (
                              <input
                                aria-label={`確認第 ${index + 1} 段屬於我`}
                                type="checkbox"
                                checked={selectedSegments.includes(id)}
                                onChange={(event) =>
                                  setSelectedSegments((previous) =>
                                    event.target.checked
                                      ? [...previous, id]
                                      : previous.filter(
                                          (value) => value !== id,
                                        ),
                                  )
                                }
                              />
                            )}
                            <div>
                              <div className="segment-meta">
                                <button
                                  className="time-button"
                                  disabled={activeRecording?.is_demo}
                                  onClick={() => {
                                    if (audioRef.current) {
                                      audioRef.current.currentTime =
                                        segment.start;
                                      void audioRef.current
                                        .play()
                                        .catch(() =>
                                          setSourceError(
                                            "請使用錄音播放器的播放按鈕開始回放。",
                                          ),
                                        );
                                    }
                                  }}
                                  aria-label={`從 ${timeLabel(segment.start)} 回放`}
                                >
                                  {!activeRecording?.is_demo && (
                                    <Play size={11} />
                                  )}{" "}
                                  {timeLabel(segment.start)} —{" "}
                                  {timeLabel(segment.end)}
                                </button>
                                <span>
                                  {confirmed ? "已確認屬於你" : "說話者未知"}
                                </span>
                              </div>
                              <p lang="en">{segment.text}</p>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {(!source || requiresSubjectConfirmation) && (
                      <button
                        className="button primary full-width"
                        disabled={!selectedSegments.length || Boolean(busy)}
                        onClick={() => void confirmSegments()}
                      >
                        <CheckCircle2 size={17} />
                        確認 {selectedSegments.length} 個片段屬於我
                      </button>
                    )}
                  </section>
                )}
                {source && (
                  <form className="source-section" onSubmit={saveMemory}>
                    <div className="source-section-title">
                      <h3>
                        <Settings2 size={17} />
                        修正理解
                      </h3>
                      <span>原始逐字稿保持獨立</span>
                    </div>
                    <label className="field-label" htmlFor="memory-edit">
                      這段經歷，應該怎樣理解？
                    </label>
                    <textarea
                      id="memory-edit"
                      value={edit}
                      onChange={(event) => setEdit(event.target.value)}
                      rows={4}
                      maxLength={4000}
                    />
                    <label className="checkbox-line">
                      <input
                        type="checkbox"
                        checked={allowFollowup}
                        onChange={(event) =>
                          setAllowFollowup(event.target.checked)
                        }
                      />
                      允許對這則記憶建立到期提醒
                    </label>
                    {allowFollowup && (
                      <label className="followup-field">
                        跟進時間（香港）
                        <input
                          type="datetime-local"
                          value={dueAt}
                          onChange={(event) => setDueAt(event.target.value)}
                          required
                        />
                      </label>
                    )}
                    <p className="small-text muted">
                      {requiresSubjectConfirmation
                        ? "請先在上方確認哪些片段屬於你，再修正記憶。"
                        : "儲存表示你核實這則記憶的理解。會建立新版本，重算卡片，並取消不適用的提示；新摘要仍會再次請你核實。"}
                    </p>
                    {conflict && (
                      <div className="conflict-message">
                        <p>
                          你的修改仍然保留。請先複製需要保留的內容，再載入最新版本。
                        </p>
                        <button
                          type="button"
                          className="small-button"
                          onClick={() => void openMemory(source.memory.id)}
                        >
                          <RefreshCw size={14} />
                          捨棄編輯並載入最新版本
                        </button>
                      </div>
                    )}
                    <div className="editor-actions">
                      <button
                        className="button primary"
                        type="submit"
                        disabled={
                          !edit.trim() ||
                          Boolean(busy) ||
                          conflict ||
                          requiresSubjectConfirmation
                        }
                      >
                        {busy === "save-memory" ? (
                          <LoaderCircle size={16} className="spin" />
                        ) : (
                          <Check size={16} />
                        )}
                        確認並儲存
                      </button>
                      <button
                        className="text-button danger-text"
                        type="button"
                        onClick={() => deleteMemory(source.memory)}
                      >
                        <Trash2 size={15} />
                        刪除記憶
                      </button>
                    </div>
                  </form>
                )}
                {source && (
                  <section className="source-section">
                    <h3 className="small-heading">記憶資料與版本</h3>
                    <div className="metadata-grid">
                      <span>
                        主體
                        <strong>
                          {source.memory.subject_id === "user"
                            ? "你（已確認）"
                            : "未知"}
                        </strong>
                      </span>
                      <span>
                        範圍
                        <strong>
                          {scopeLabels[source.memory.scope] ||
                            (source.memory.scope ? "錄音情境" : "未指定")}
                        </strong>
                      </span>
                      <span>
                        事件時間
                        <strong>{shortDate(source.memory.event_time)}</strong>
                      </span>
                      <span>
                        狀態
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
                                ? "目前版本"
                                : "已取代"}
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
                    刪除整段錄音及相關記憶
                  </button>
                )}
              </>
            )}
          </div>
          <div className="drawer-foot">
            <button className="text-button" onClick={closeDrawer}>
              <ArrowLeft size={15} />
              返回{viewNames[view]}
            </button>
            <span>保留原日期與頁面</span>
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
                取消
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
                    "已刪除，相關理解已更新。",
                  )
                }
              >
                {busy === "delete" ? "正在刪除…" : "確認刪除"}
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
  return (
    <Modal title="偏好與模型" close={close}>
      <div className="modal-body">
        {error && (
          <div className="alert alert-error" role="alert">
            {error}
          </div>
        )}
        <p className="modal-description">
          選擇你希望收到的支持。偏好更改會套用到待展示的提示。
        </p>
        <div className="preference-list">
          {(["reminder", "suggestion", "comfort", "warning"] as CareType[]).map(
            (type) => (
              <label key={type} className="preference-row">
                <span>
                  <strong>{careLabels[type]}</strong>
                  <small>
                    {type === "warning"
                      ? "僅虛構預警演示，不作疾病或風險判斷"
                      : type === "reminder"
                        ? "只跟進已確認並允許提醒的事項"
                        : type === "suggestion"
                          ? "在清楚有需要時，提供可行的小步驟"
                          : "在你表達需要時，給一點支持"}
                  </small>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  disabled={busy}
                  checked={Boolean(settings.preferences?.[type])}
                  onChange={(event) =>
                    save({
                      preferences: {
                        ...settings.preferences,
                        [type]: event.target.checked,
                      },
                    })
                  }
                />
              </label>
            ),
          )}
        </div>
        <label className="preference-row">
          <span>
            <strong>免打擾</strong>
            <small>開啟時保持安靜，暫停所有主動提示</small>
          </span>
          <input
            type="checkbox"
            role="switch"
            disabled={busy}
            checked={Boolean(settings.quiet_mode)}
            onChange={(event) => save({ quiet_mode: event.target.checked })}
          />
        </label>
        <label className="preference-row">
          <span>
            <strong>允許虛構預警演示</strong>
            <small>須與「預警」同時開啟；真實錄音不產生臨床預警</small>
          </span>
          <input
            type="checkbox"
            role="switch"
            disabled={busy}
            checked={Boolean(settings.warning_demo_enabled)}
            onChange={(event) =>
              save({ warning_demo_enabled: event.target.checked })
            }
          />
        </label>
        <div className="preferences-fields">
          <label>
            提示最短間隔（分鐘）
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
            保留期限（天）
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
            <strong>本機轉錄模型</strong>
            <span className="tag">開源</span>
          </div>
          <p>Whisper large-v3-turbo · faster-whisper</p>
          <dl>
            <div>
              <dt>狀態</dt>
              <dd>
                {status.loaded
                  ? "已載入"
                  : status.available
                    ? "首次使用時載入"
                    : String(status.status || status.state || "等待設定")}
              </dd>
            </div>
            <div>
              <dt>運行裝置</dt>
              <dd>{String(status.device || "由後端選擇")}</dd>
            </div>
            <div>
              <dt>分析模式</dt>
              <dd>本機規則示例</dd>
            </div>
          </dl>
          <small>
            首次使用可能需要下載模型。英文逐字稿可回看與修正；說話者身份由你確認。
          </small>
        </div>
        <DatasetPanel />
      </div>
    </Modal>
  );
}
