"""One local HTTP server: audio upload, evidence, memory, care, and compiled UI."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .asr import Transcriber, inspect_audio
from .engine import Engine, NotFoundError, VersionConflict
from .dataset import DatasetStore, DatasetNotFoundError
from .llm import TextAnalyzer

ROOT = Path(__file__).resolve().parents[2]
HK = timezone(timedelta(hours=8))
LOG = logging.getLogger("hya")
load_dotenv(ROOT / ".env")


class Confirmation(BaseModel):
    segment_ids: list[str] | None = None


class Revision(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    version: int = Field(ge=1)
    confirmed: bool = True
    follow_up: dict | None = None


class DayRequest(BaseModel):
    date: str | None = None


class Scenario(BaseModel):
    type: Literal["reminder", "suggestion", "comfort", "warning", "none", "multiple", "sleep", "social_history", "emotional_history"]


class CardReview(BaseModel):
    expected_versions: dict[str, Annotated[int, Field(strict=True, ge=1)]]
    review_token: str = Field(min_length=1, max_length=128)


class BedtimeRequest(BaseModel):
    due_at: str = Field(min_length=1, max_length=80)
    expected_versions: dict[str, Annotated[int, Field(strict=True, ge=1)]]


class Feedback(BaseModel):
    action: Literal["snooze", "dismiss", "done", "helpful"]


def valid_date(value):
    if value:
        try:
            Date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(422, "日期格式須為 YYYY-MM-DD。") from exc
    return value


def recording_time(value):
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=HK).isoformat() if parsed.tzinfo is None else parsed.astimezone(HK).isoformat()
    except ValueError as exc:
        raise HTTPException(422, "錄音時間格式不正確。") from exc


def create_app(data_dir: str | Path | None = None, transcriber=None, analyzer=None):
    chosen = Path(data_dir or os.getenv("HYA_DATA_DIR", str(ROOT / "data")))
    if not chosen.is_absolute():
        chosen = ROOT / chosen
    chosen.mkdir(parents=True, exist_ok=True)
    audio_dir = chosen / "audio"
    audio_dir.mkdir(exist_ok=True)
    processing_dir = chosen / "processing"
    processing_dir.mkdir(exist_ok=True)
    engine = Engine(chosen)
    datasets = DatasetStore(chosen)
    asr = transcriber or Transcriber(model_name=os.getenv("HYA_ASR_MODEL", "large-v3-turbo"),
                                    device=os.getenv("HYA_ASR_DEVICE", "auto"),
                                    compute_type=os.getenv("HYA_ASR_COMPUTE_TYPE", "auto"),
                                    model_dir=os.getenv("HYA_ASR_MODEL_DIR") or None)
    llm = analyzer or TextAnalyzer()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hya-transcription")
    jobs = set()
    job_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        engine.cleanup_retention()
        # Interrupted work can be retried; do not present old processing state as live.
        for rec in engine.list_recordings():
            if rec["status"] in {"queued", "processing"}:
                engine.set_recording_status(rec["id"], "failed", "上次處理已中斷，可以重新轉錄。")
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Hear You Are", version="0.2.0", lifespan=lifespan)
    app.state.engine, app.state.transcriber, app.state.analyzer = engine, asr, llm
    app.state.datasets = datasets
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "::1", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8765", "http://127.0.0.1:8765"],
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type"])

    @app.exception_handler(VersionConflict)
    async def stale(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NotFoundError)
    async def missing(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DatasetNotFoundError)
    async def missing_dataset(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    def run_asr(rec_id, language):
        try:
            rec = engine.get_recording(rec_id)
            engine.set_recording_status(rec_id, "processing")
            # Work only on a transient copy. Original bytes remain unchanged;
            # decoder resampling and VAD do not rewrite the evidence timeline.
            with tempfile.TemporaryDirectory(prefix="job-", dir=processing_dir) as scratch:
                processing_copy = Path(scratch) / Path(rec["file_path"]).name
                shutil.copyfile(rec["file_path"], processing_copy)
                result = asr.transcribe(processing_copy, language=language)
            # Source could have been deleted during processing: don't resurrect it.
            engine.get_recording(rec_id)
            engine.ingest_segments(rec_id, result["segments"], language=result.get("language", language), subject_confirmed=False)
            engine.set_recording_status(rec_id, "completed", metrics=result.get("metrics", {}))
        except NotFoundError:
            pass
        except Exception as exc:
            LOG.warning("Transcription job failed (%s)", type(exc).__name__)
            try:
                engine.set_recording_status(rec_id, "failed", f"轉錄未完成（{type(exc).__name__}）；請檢查模型、裝置及錄音格式，然後重試。")
            except NotFoundError:
                pass
        finally:
            with job_lock:
                jobs.discard(rec_id)

    def enqueue(rec_id, language="en"):
        with job_lock:
            if rec_id in jobs:
                return
            jobs.add(rec_id)
        engine.set_recording_status(rec_id, "queued")
        executor.submit(run_asr, rec_id, language)

    @app.get("/api/health")
    def health():
        status = asr.health()
        status["status"] = status.get("state", status.get("status", "ready"))
        return {"status": "ok", "version": "0.2.0", "asr": status, "analysis": llm.status(), "memory_layers": 5}

    @app.get("/api/state")
    def state(date: str | None = None):
        engine.cleanup_retention()
        selected = valid_date(date)
        engine.evaluate(date=selected)
        return engine.state(selected)

    @app.get("/api/recordings")
    def recordings():
        return engine.list_recordings()

    @app.get("/api/recordings/{rec_id}")
    def recording(rec_id: str):
        return engine.get_recording(rec_id)

    @app.post("/api/recordings")
    async def upload(file: UploadFile = File(...), recorded_at: str | None = Form(None), language: str = Form("en")):
        when = recording_time(recorded_at)
        if language not in {"en", "auto"}:
            raise HTTPException(422, "本 MVP 提供英文或自動語言辨識。")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4"}:
            raise HTTPException(415, "請選擇 WAV、MP3、M4A、FLAC、OGG、WEBM 或 MP4 錄音。")
        dest = audio_dir / (uuid.uuid4().hex + suffix)
        hasher, size = hashlib.sha256(), 0
        maximum = int(os.getenv("HYA_MAX_UPLOAD_MB", "100")) * 1024 * 1024
        try:
            with dest.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise HTTPException(413, "檔案超過上傳大小限制。")
                    hasher.update(chunk)
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "錄音檔案是空的。")
            try:
                audio_info = inspect_audio(dest, max_seconds=float(os.getenv("HYA_MAX_AUDIO_MINUTES", "20")) * 60)
            except Exception as exc:
                raise HTTPException(422, "無法讀取這個音訊檔案，請檢查格式或重新匯出。") from exc
            if audio_info["duration"] > float(os.getenv("HYA_MAX_AUDIO_MINUTES", "20")) * 60:
                raise HTTPException(422, "錄音超過 MVP 的時長限制，請先分段。")
            rec = engine.create_recording(file.filename or "錄音", str(dest), hasher.hexdigest(), when)
            if rec.get("duplicate"):
                dest.unlink(missing_ok=True)
            else:
                enqueue(rec["id"], language)
            return {"recording": engine.get_recording(rec["id"]), "duplicate": bool(rec.get("duplicate"))}
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.post("/api/recordings/{rec_id}/retry")
    def retry(rec_id: str):
        rec = engine.get_recording(rec_id)
        if rec["status"] not in {"failed", "queued"}:
            raise HTTPException(409, "這個錄音目前不需要重試。")
        if not rec.get("file_path") or not Path(rec["file_path"]).is_file():
            raise HTTPException(422, "原始錄音已不存在，請重新匯入。")
        enqueue(rec_id, rec.get("language") or "en")
        return engine.get_recording(rec_id)

    @app.post("/api/recordings/{rec_id}/confirm")
    def confirm(rec_id: str, body: Confirmation):
        result = engine.confirm_recording(rec_id, body.segment_ids)
        engine.evaluate()
        return result

    @app.delete("/api/recordings/{rec_id}")
    def remove_recording(rec_id: str):
        return engine.delete_recording(rec_id)

    @app.get("/api/recordings/{rec_id}/audio")
    def audio(rec_id: str):
        rec = engine.get_recording(rec_id)
        path = Path(rec.get("file_path") or "").resolve()
        if not path.is_relative_to(audio_dir.resolve()) or not path.is_file():
            raise HTTPException(404, "這項示例沒有原始錄音，或錄音已刪除。")
        return FileResponse(path, filename=rec.get("filename", "recording.wav"), content_disposition_type="inline")

    @app.get("/api/memories/{memory_id}/source")
    def source(memory_id: str):
        return engine.get_source(memory_id)

    @app.get("/api/memories")
    def search(query: str = "", date: str | None = None, person: str | None = None):
        return engine.retrieve(query=query, date=valid_date(date), person=person)

    @app.patch("/api/memories/{memory_id}")
    def revise(memory_id: str, body: Revision):
        if not body.content.strip():
            raise HTTPException(422, "記憶內容不能是空白。")
        result = engine.revise_memory(memory_id, **body.model_dump())
        engine.evaluate()
        return result

    @app.delete("/api/memories/{memory_id}")
    def remove_memory(memory_id: str):
        return engine.delete_memory(memory_id)

    @app.patch("/api/settings")
    def preferences(body: dict):
        for field, low, high in (("frequency_minutes", 0, 1440), ("retention_days", 1, 3650)):
            if field in body and (type(body[field]) is not int or not low <= body[field] <= high):
                raise HTTPException(422, f"{field} 數值超出可用範圍。")
        return engine.settings_patch(body)

    @app.post("/api/proactive/evaluate")
    def evaluate(body: DayRequest):
        result = engine.evaluate(date=valid_date(body.date))
        if llm.status()["configured"]:
            for card in result["cards"]:
                if card.get("generation_mode") == "llm":
                    continue
                current = engine.state(card["date"])
                memories = [m for m in current["memories"] if m["id"] in card["memory_ids"]]
                try:
                    text = llm.care_text(card, memories)
                    engine.apply_proactive_text(card["id"], **text, expected_versions=card["versions"])
                except VersionConflict:
                    return engine.evaluate(date=body.date)
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    result["analysis_note"] = "模型文案未完成；保留已通過條件的本機模板。"
            refreshed = engine.evaluate(date=body.date)
            if "analysis_note" in result:
                refreshed["analysis_note"] = result["analysis_note"]
            return refreshed
        return result

    @app.post("/api/proactive/{card_id}/feedback")
    def feedback(card_id: str, body: Feedback):
        return engine.feedback(card_id, body.action)

    @app.post("/api/cards/{card_id}/confirm")
    def review_card(card_id: str, body: CardReview):
        result = engine.confirm_card(card_id, body.expected_versions, body.review_token)
        engine.evaluate(date=result["date"])
        return engine.state(result["date"])

    @app.post("/api/proactive/{card_id}/bedtime")
    def bedtime(card_id: str, body: BedtimeRequest):
        result = engine.schedule_bedtime(card_id, recording_time(body.due_at), body.expected_versions)
        engine.evaluate(date=result["date"])
        return engine.state(result["date"])

    @app.get("/api/datasets")
    def dataset_list():
        return {"datasets": datasets.list(), "purpose": "保留後期後訓練資料入口；所有匯入待專業審核，未投入訓練或即時推理。",
                "training_enabled": False, "runtime_enabled": False}

    @app.post("/api/datasets")
    async def dataset_upload(file: UploadFile = File(...)):
        try:
            if Path(file.filename or "").suffix.lower() != ".jsonl":
                raise HTTPException(415, "請選擇 JSONL 格式的資料集。")
            raw = await file.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                raise HTTPException(413, "資料集超過 5 MB 上限。")
            return datasets.import_file(file.filename or "dataset.jsonl", raw)
        finally:
            await file.close()

    @app.get("/api/datasets/{dataset_id}")
    def dataset_detail(dataset_id: str):
        return datasets.get(dataset_id)

    @app.delete("/api/datasets/{dataset_id}")
    def dataset_delete(dataset_id: str):
        return datasets.delete(dataset_id)

    @app.post("/api/demo/seed")
    def seed():
        return engine.seed_demo()

    @app.post("/api/demo/scenario")
    def scenario(body: Scenario):
        return engine.set_demo_scenario(body.type)

    @app.get("/api/analysis/status")
    def analysis_status():
        return llm.status()

    @app.post("/api/analysis/generate")
    def analyze(body: DayRequest):
        snapshot = engine.state(valid_date(body.date))
        selected_ids = {ident for card in snapshot["cards"] for ident in card["memory_ids"]}
        memories = [m for m in snapshot["memories"] if m["id"] in selected_ids]
        versions = {m["id"]: m["version"] for m in memories}
        try:
            outputs = llm.summarize(memories)
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise HTTPException(502, "文字模型未回應或格式不符；原卡片已保留。請檢查本機模型／設定。") from exc
        return engine.apply_card_summaries(snapshot["date"], outputs, versions)

    # API registered first; unknown /api paths must never fall through to the SPA.
    @app.api_route("/api/{unknown:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    def unknown_api(unknown: str):
        raise HTTPException(404, "找不到這個 API。")

    built = ROOT / "web" / "dist"
    if built.exists():
        app.mount("/", StaticFiles(directory=built, html=True), name="web")
    else:
        @app.get("/")
        def unbuilt():
            return {"message": "前端尚未建置。請在 web 目錄執行 npm run build，或使用開發伺服器。"}
    return app


app = create_app()
