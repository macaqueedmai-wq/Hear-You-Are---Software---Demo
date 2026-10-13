import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Database,
  FileText,
  LoaderCircle,
  RefreshCw,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { api } from "../api";

interface DatasetRecord {
  kind: "knowledge" | "counselling_case" | "guidance";
  title: string;
  content: string;
  source: string;
  license: string;
  deidentified: boolean;
  consent_to_use: boolean;
  professional_reviewer?: string;
}
interface Dataset {
  id: string;
  filename: string;
  item_count: number;
  status: string;
  training_enabled: boolean;
  runtime_enabled: boolean;
  created_at: string;
  items?: DatasetRecord[];
}
const kinds: Record<string, string> = {
  knowledge: "知識",
  counselling_case: "諮詢案例",
  guidance: "指引",
};
const example = JSON.stringify({
  kind: "guidance",
  title: "虛構格式示例：休息提醒",
  content: "此內容只用於展示欄位格式，不是專業建議或訓練資料。",
  source: "本產品虛構格式示例",
  license: "CC0-1.0",
  deidentified: true,
  consent_to_use: true,
});

export function DatasetPanel() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [detail, setDetail] = useState<Dataset | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [showExample, setShowExample] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  async function refresh() {
    setLoading(true);
    try {
      const result = await api<{ datasets: Dataset[] }>("/datasets");
      setDatasets(result.datasets || []);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "未能讀取資料集。");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  async function upload() {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      setError("JSONL 超過 5 MB，請減少內容後再匯入。");
      return;
    }
    setBusy("upload");
    setError("");
    setNotice("");
    const body = new FormData();
    body.append("file", file);
    try {
      await api("/datasets", { method: "POST", body });
      setFile(null);
      if (input.current) input.current.value = "";
      await refresh();
      setNotice("已保存至本機，待專業審核。訓練與執行仍未啟用。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "未能匯入資料集。");
    } finally {
      setBusy("");
    }
  }
  async function open(id: string) {
    setBusy(id);
    setError("");
    try {
      setDetail(await api<Dataset>(`/datasets/${id}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "未能讀取明細。");
    } finally {
      setBusy("");
    }
  }
  async function remove(id: string) {
    setBusy(id);
    setError("");
    try {
      await api(`/datasets/${id}`, { method: "DELETE" });
      if (detail?.id === id) setDetail(null);
      setDeleting(null);
      await refresh();
      setNotice("資料集及本機檔案已刪除。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "未能刪除資料集。");
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="dataset-panel">
      <div className="dataset-heading">
        <h3>
          <Database size={17} />
          專業資料集入口
        </h3>
        <button
          className="icon-button"
          aria-label="重新整理資料集"
          onClick={() => void refresh()}
          disabled={Boolean(busy)}
        >
          <RefreshCw size={15} />
        </button>
      </div>
      <p className="small-text muted">
        為後期後訓練預留的本機資料入口。匯入內容均待專業審核，不會自動傳送給模型、訓練或用於預警。
      </p>
      <div className="dataset-badges">
        <span className="tag">待專業審核</span>
        <span className="tag">未訓練</span>
        <span className="tag">未啟用</span>
      </div>
      {error && (
        <div className="alert alert-error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="alert alert-success" role="status">
          <CheckCircle2 size={16} />
          {notice}
        </div>
      )}
      <label className="dataset-file-label">
        選擇本機 JSONL 檔案
        <input
          ref={input}
          type="file"
          accept=".jsonl,.ndjson,application/json"
          onChange={(event) => setFile(event.target.files?.[0] || null)}
        />
      </label>
      <div className="dataset-upload-actions">
        <small>最多 5 MB／1000 筆 · 必須去識別並獲授權</small>
        <button
          className="small-button"
          disabled={!file || Boolean(busy)}
          onClick={() => void upload()}
        >
          {busy === "upload" ? (
            <LoaderCircle size={15} className="spin" />
          ) : (
            <UploadCloud size={15} />
          )}
          匯入並等待審核
        </button>
      </div>
      <button
        className="text-button"
        onClick={() => setShowExample((previous) => !previous)}
      >
        <FileText size={14} />
        {showExample ? "收起格式說明" : "查看 JSONL 格式與虛構示例"}
      </button>
      {showExample && (
        <div className="dataset-example">
          <p>
            每行一個 JSON 物件，必填
            kind、title、content、source、license、deidentified:true、consent_to_use:true。kind
            可為 knowledge、counselling_case、guidance；professional_reviewer
            選填。
          </p>
          <strong>以下僅為虛構格式示例，不是專業資料：</strong>
          <pre>{example}</pre>
        </div>
      )}
      {loading ? (
        <div className="dataset-loading" role="status">
          <LoaderCircle size={16} className="spin" />
          正在讀取本機資料集…
        </div>
      ) : datasets.length === 0 ? (
        <p className="dataset-empty">
          尚未匯入資料集。只有通過欄位及授權檢查的檔案才會保存。
        </p>
      ) : (
        <div className="dataset-list">
          {datasets.map((dataset) => (
            <article key={dataset.id} className="dataset-item">
              <div>
                <strong>{dataset.filename}</strong>
                <small>
                  {dataset.item_count} 筆 · 待專業審核 · 未訓練 · 未啟用
                </small>
              </div>
              <button
                className="text-button"
                disabled={Boolean(busy)}
                onClick={() => void open(dataset.id)}
              >
                明細
              </button>
              <button
                className="icon-button"
                aria-label={`刪除資料集 ${dataset.filename}`}
                disabled={Boolean(busy)}
                onClick={() => setDeleting(dataset.id)}
              >
                <Trash2 size={15} />
              </button>
              {deleting === dataset.id && (
                <div className="dataset-delete-confirm">
                  <p>確定永久刪除這個本機資料集及檔案？刪除後無法復原。</p>
                  <div>
                    <button
                      className="small-button ghost"
                      onClick={() => setDeleting(null)}
                    >
                      取消
                    </button>
                    <button
                      className="small-button"
                      disabled={Boolean(busy)}
                      onClick={() => void remove(dataset.id)}
                    >
                      確認刪除
                    </button>
                  </div>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
      {detail && (
        <div className="dataset-detail">
          <div className="dataset-heading">
            <h4>{detail.filename}</h4>
            <button
              className="icon-button"
              aria-label="關閉資料集明細"
              onClick={() => setDetail(null)}
            >
              <X size={16} />
            </button>
          </div>
          <p className="small-text muted">
            待專業審核 · 未訓練 · 未啟用。提供審核人名稱不代表已完成專業審核。
          </p>
          {detail.items?.map((record, index) => (
            <article key={index}>
              <span className="tag">{kinds[record.kind] || "資料"}</span>
              <h4>{record.title}</h4>
              <p>{record.content}</p>
              <dl>
                <div>
                  <dt>來源</dt>
                  <dd>{record.source}</dd>
                </div>
                <div>
                  <dt>授權</dt>
                  <dd>{record.license}</dd>
                </div>
                <div>
                  <dt>去識別／獲准使用</dt>
                  <dd>
                    {record.deidentified && record.consent_to_use
                      ? "均已聲明"
                      : "資料不完整"}
                  </dd>
                </div>
                {record.professional_reviewer && (
                  <div>
                    <dt>所填審核人</dt>
                    <dd>{record.professional_reviewer}（未核實）</dd>
                  </div>
                )}
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
