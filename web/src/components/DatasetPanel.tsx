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
  knowledge: "Knowledge",
  counselling_case: "Counselling case",
  guidance: "Guidance",
};
const example = JSON.stringify({
  kind: "guidance",
  title: "Sample format example: rest reminder",
  content:
    "This content only demonstrates the field format. It is not professional advice or training data.",
  source: "Sample format example from this product",
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
      setError(err instanceof Error ? err.message : "Could not load datasets.");
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
      setError("The JSONL file exceeds 5 MB. Reduce the content, then import again.");
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
      setNotice("Saved locally and awaiting professional review. Training and runtime use remain disabled.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not import the dataset.");
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
      setError(err instanceof Error ? err.message : "Could not load the details.");
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
      setNotice("Dataset and local files deleted.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the dataset.");
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="dataset-panel">
      <div className="dataset-heading">
        <h3>
          <Database size={17} />
          Professional dataset intake
        </h3>
        <button
          className="icon-button"
          aria-label="Refresh datasets"
          onClick={() => void refresh()}
          disabled={Boolean(busy)}
        >
          <RefreshCw size={15} />
        </button>
      </div>
      <p className="small-text muted">
        A local intake reserved for later post-training. Imported content awaits professional review and is never sent to a model, used for training, or used for proactive care automatically.
      </p>
      <div className="dataset-badges">
        <span className="tag">Awaiting professional review</span>
        <span className="tag">Not trained</span>
        <span className="tag">Disabled</span>
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
        Choose a local JSONL file
        <input
          ref={input}
          type="file"
          accept=".jsonl,.ndjson,application/json"
          onChange={(event) => setFile(event.target.files?.[0] || null)}
        />
      </label>
      <div className="dataset-upload-actions">
        <small>Up to 5 MB / 1000 items · Must be de-identified and licensed</small>
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
          Import for review
        </button>
      </div>
      <button
        className="text-button"
        onClick={() => setShowExample((previous) => !previous)}
      >
        <FileText size={14} />
        {showExample ? "Hide format guide" : "View the JSONL format and a sample"}
      </button>
      {showExample && (
        <div className="dataset-example">
          <p>
            One JSON object per line. Required fields: kind, title, content, source, license,
            deidentified:true and consent_to_use:true. kind can be knowledge,
            counselling_case or guidance; professional_reviewer is optional.
          </p>
          <strong>This is only a sample format example, not professional data:</strong>
          <pre>{example}</pre>
        </div>
      )}
      {loading ? (
        <div className="dataset-loading" role="status">
          <LoaderCircle size={16} className="spin" />
          Loading local datasets…
        </div>
      ) : datasets.length === 0 ? (
        <p className="dataset-empty">
          No datasets imported yet. Only files that pass the field and licence checks are kept.
        </p>
      ) : (
        <div className="dataset-list">
          {datasets.map((dataset) => (
            <article key={dataset.id} className="dataset-item">
              <div>
                <strong>{dataset.filename}</strong>
                <small>
                  {dataset.item_count} items · Awaiting professional review · Not trained · Disabled
                </small>
              </div>
              <button
                className="text-button"
                disabled={Boolean(busy)}
                onClick={() => void open(dataset.id)}
              >
                Details
              </button>
              <button
                className="icon-button"
                aria-label={`Delete dataset ${dataset.filename}`}
                disabled={Boolean(busy)}
                onClick={() => setDeleting(dataset.id)}
              >
                <Trash2 size={15} />
              </button>
              {deleting === dataset.id && (
                <div className="dataset-delete-confirm">
                  <p>Permanently delete this local dataset and its files? This cannot be undone.</p>
                  <div>
                    <button
                      className="small-button ghost"
                      onClick={() => setDeleting(null)}
                    >
                      Cancel
                    </button>
                    <button
                      className="small-button"
                      disabled={Boolean(busy)}
                      onClick={() => void remove(dataset.id)}
                    >
                      Delete permanently
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
              aria-label="Close dataset details"
              onClick={() => setDetail(null)}
            >
              <X size={16} />
            </button>
          </div>
          <p className="small-text muted">
            Awaiting professional review · Not trained · Disabled. Naming a reviewer does not mean professional review is complete.
          </p>
          {detail.items?.map((record, index) => (
            <article key={index}>
              <span className="tag">{kinds[record.kind] || "Data"}</span>
              <h4>{record.title}</h4>
              <p>{record.content}</p>
              <dl>
                <div>
                  <dt>Source</dt>
                  <dd>{record.source}</dd>
                </div>
                <div>
                  <dt>Licence</dt>
                  <dd>{record.license}</dd>
                </div>
                <div>
                  <dt>De-identified / consent to use</dt>
                  <dd>
                    {record.deidentified && record.consent_to_use
                      ? "Both declared"
                      : "Incomplete data"}
                  </dd>
                </div>
                {record.professional_reviewer && (
                  <div>
                    <dt>Declared reviewer</dt>
                    <dd>{record.professional_reviewer} (unverified)</dd>
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
