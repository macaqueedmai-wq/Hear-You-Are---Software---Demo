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
from pydantic import BaseModel, ConfigDict, Field

from .asr import Transcriber, TranscriptionError, inspect_audio
from .cloud_asr import TencentTranscriber
from .qwen_asr import QwenTranscriber
from .sensevoice_asr import SenseVoiceTranscriber
from .memory_agents import MemoryAgents, AgentError
from .pipeline import organize_recording, understand_recording
from .engine import Engine, NotFoundError, VersionConflict
from .dataset import DatasetStore, DatasetNotFoundError
from .llm import TextAnalyzer
from .daily_summaries import DailySummaryCoordinator, SummaryGenerationError

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


class TopicMemoryRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    version: int = Field(strict=True, ge=1)
    content: str = Field(min_length=1, max_length=4000)


class TopicRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=2000)
    memories: list[TopicMemoryRevision] | None = Field(default=None, max_length=100)


class DayRequest(BaseModel):
    date: str | None = None


class SupportRequest(DayRequest):
    model_config = ConfigDict(extra="forbid")
    type: Literal["reminder", "suggestion", "comfort", "warning"]


class DisplayTimeRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["reminder", "suggestion", "comfort", "warning"]
    display_at: str | None
    expected_display_at: str | None


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
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, "Recording time must be a valid ISO date and time.") from exc


def create_app(data_dir: str | Path | None = None, transcriber=None, analyzer=None, memory_agents=None):
    deployment_mode = os.getenv("HYA_DEPLOYMENT_MODE", "local").strip().lower()
    if deployment_mode not in {"local", "server"}:
        raise ValueError("HYA_DEPLOYMENT_MODE must be local or server")
    allowed_hosts = [host.strip() for host in os.getenv(
        "HYA_ALLOWED_HOSTS", "localhost,127.0.0.1,::1,testserver"
    ).split(",") if host.strip()]
    if not allowed_hosts:
        raise ValueError("HYA_ALLOWED_HOSTS must contain at least one hostname")
    chosen = Path(data_dir or os.getenv("HYA_DATA_DIR", str(ROOT / "data")))
    if not chosen.is_absolute():
        chosen = ROOT / chosen
    chosen.mkdir(parents=True, exist_ok=True)
    audio_dir = chosen / "audio"
    audio_dir.mkdir(exist_ok=True)
    processing_dir = chosen / "processing"
    processing_dir.mkdir(exist_ok=True)
    memory_mode = os.getenv("HYA_MEMORY_MODE", "deepseek").strip()
    if memory_mode not in {"local_rules", "deepseek"}:
        raise ValueError("HYA_MEMORY_MODE must be local_rules or deepseek")
    engine = Engine(chosen, model_mode=memory_mode == "deepseek")
    datasets = DatasetStore(chosen)
    provider = os.getenv("HYA_ASR_PROVIDER", "sensevoice").strip()
    if transcriber is not None:
        asr = transcriber
    elif provider == "sensevoice":
        asr = SenseVoiceTranscriber()
    elif provider == "qwen":
        asr = QwenTranscriber()
    elif provider == "tencent":
        asr = TencentTranscriber()
    elif provider == "whisper":
        asr = Transcriber(model_name=os.getenv("HYA_ASR_MODEL", "large-v3-turbo"),
                          device=os.getenv("HYA_ASR_DEVICE", "auto"),
                          compute_type=os.getenv("HYA_ASR_COMPUTE_TYPE", "auto"),
                          model_dir=os.getenv("HYA_ASR_MODEL_DIR") or None)
    else:
        raise ValueError("HYA_ASR_PROVIDER must be sensevoice, qwen, tencent or whisper; providers never fall back silently")
    llm = analyzer or TextAnalyzer()
    agents = memory_agents or MemoryAgents()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hya-transcription")
    care_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hya-care")
    care_jobs = {}
    care_lock = threading.Lock()
    daily_summaries = DailySummaryCoordinator(engine, llm, automatic=memory_mode == "deepseek")
    jobs = set()
    job_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        engine.cleanup_retention()
        # Interrupted work can be retried; do not present old processing state as live.
        for rec in engine.list_recordings():
            if rec["status"] in {"queued", "processing"}:
                engine.set_recording_status(rec["id"], "failed", "上次處理已中斷，可以重新轉錄。")
            interrupted = rec.get("memory_pipeline", {})
            if interrupted.get("status") in {"pending", "processing"}:
                engine.update_pipeline(rec["id"], {
                    "status": "failed", "stage": interrupted.get("stage", "pending"),
                    "model": interrupted.get("model"), "stages": [],
                    "error": "Previous memory processing was interrupted; retry the existing transcript.",
                })
            if rec.get("topic_pipeline", {}).get("status") in {"pending", "processing"}:
                engine.update_topic_pipeline(rec["id"], {
                    "status": "failed", "stage": "consolidation",
                    "error": "Previous topic organization was interrupted; organize the existing transcript again.",
                })
        yield
        executor.shutdown(wait=False, cancel_futures=True)
        care_executor.shutdown(wait=False, cancel_futures=True)
        daily_summaries.shutdown()

    app = FastAPI(title="Hear You Are", version="0.2.0", lifespan=lifespan)
    app.state.engine, app.state.transcriber, app.state.analyzer = engine, asr, llm
    app.state.memory_agents = agents
    app.state.datasets = datasets
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
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

    def generate_care(card):
        """Network work runs outside Engine transactions; commit rechecks sources."""
        try:
            revision = card.get("display_schedule_revision", 0)
            current = engine.proactive_text_snapshot(card["id"], card["versions"], revision)
            if current["card"].get("generation_mode") == "llm":
                return {"status": "ready", "message": "Support is ready."}
            text = llm.care_text(current["card"], current["memories"])
            engine.apply_proactive_text(card["id"], **text, expected_versions=card["versions"],
                                        expected_display_revision=revision)
            return {"status": "ready", "message": "Support is ready."}
        except (VersionConflict, NotFoundError):
            return {"status": "pending", "message": "Sources changed; support will be checked again."}
        except Exception as error:
            LOG.warning("Care generation failed (%s)", type(error).__name__)
            return {"status": "failed", "message": "DeepSeek care generation failed; retry support generation.",
                    "http_status": 502}

    def automatic_care(date=None, *, explicit=False, requested_id=None):
        """Singleflight per immutable candidate; polling never retries failures."""
        result = engine.evaluate(date=valid_date(date))
        if requested_id is not None:
            result = {**result, "cards": [card for card in result["cards"] if card["id"] == requested_id]}
        candidates = [card for card in result["cards"] if card.get("generation_mode") != "llm"]
        generation = {"status": "ready" if result["cards"] else "idle",
                      "message": "Support is ready." if result["cards"] else result["assessment"]["reason"]}
        if candidates and not llm.status().get("configured"):
            if memory_mode == "deepseek":
                generation = {"status": "unconfigured", "message": "DeepSeek care generation is not configured; check the text-model settings.",
                              "http_status": 503}
        elif candidates:
            futures = []
            for card in candidates:
                key = (card["id"], tuple(sorted(card["versions"].items())), card.get("display_schedule_revision"))
                with care_lock:
                    future = care_jobs.get(key)
                    if future is None or explicit and future.done() and future.result().get("status") in {"failed", "pending"}:
                        future = care_executor.submit(generate_care, card)
                        care_jobs[key] = future
                    # Finished old signatures need no permanent process memory.
                    if len(care_jobs) > 128:
                        for previous in list(care_jobs):
                            if previous != key and care_jobs[previous].done():
                                care_jobs.pop(previous)
                                if len(care_jobs) <= 128:
                                    break
                futures.append(future)
            generation = {"status": "pending", "message": "Conditions are met; support is being generated automatically."}
            if explicit or all(future.done() for future in futures):
                outcomes = [future.result() for future in futures]
                generation = next((outcome for outcome in outcomes if outcome["status"] != "ready"), outcomes[0])
                result = engine.evaluate(date=date)
                if requested_id is not None:
                    result = {**result, "cards": [card for card in result["cards"] if card["id"] == requested_id]}
        if explicit and generation.get("http_status") and memory_mode == "deepseek":
            raise HTTPException(generation["http_status"], generation["message"])
        if generation["status"] == "ready" and not result["cards"]:
            generation = {"status": "idle", "message": result["assessment"]["reason"]}
        if memory_mode == "deepseek":
            generated = [card for card in result["cards"] if card.get("generation_mode") == "llm"]
            if generation["status"] == "ready" and len(generated) != len(result["cards"]):
                generation = {"status": "pending", "message": "Sources changed; support will be checked again."}
            result = {**result, "cards": generated}
        public_generation = {key: generation[key] for key in ("status", "message")}
        result = {**result, "care_generation": public_generation}
        if generation["status"] in {"pending", "failed", "unconfigured"}:
            result["analysis_note"] = generation["message"]
            result["assessment"] = {**result["assessment"], "reason": generation["message"]}
        return result

    def application_state(date=None):
        care = automatic_care(date)
        snapshot = daily_summaries.state(date)
        # A worker may finish between the nonblocking care check and this final
        # state snapshot. Keep times/conditions from the same cards shown here.
        assessment = {**care["assessment"], "types": snapshot["assessment"]["types"]}
        snapshot.update(assessment=assessment, care_generation=care["care_generation"])
        if care.get("analysis_note"):
            snapshot["analysis_note"] = care["analysis_note"]
        return snapshot

    def summaries_for_recording(recording):
        stamp = recording.get("recorded_at")
        selected = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(HK).date().isoformat() if stamp else None
        return daily_summaries.state(selected)

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
            engine.ingest_segments(rec_id, result["segments"], language=result.get("language", language),
                                   subject_confirmed=False, extract=memory_mode != "deepseek")
            engine.set_recording_status(rec_id, "completed", metrics=result.get("metrics", {}))
            if memory_mode == "deepseek":
                try:
                    understand_recording(engine, agents, rec_id)
                    summaries_for_recording(engine.get_recording(rec_id))
                    automatic_care()
                except NotFoundError:
                    pass
                except Exception as error:
                    # A failed memory stage must not mislabel a successful ASR job.
                    LOG.warning("Memory job failed (%s)", type(error).__name__)
        except NotFoundError:
            pass
        except Exception as exc:
            LOG.warning("Transcription job failed (%s)", type(exc).__name__,
                        exc_info=asr.health().get("provider") != "tencent")
            # ASR errors contain curated user-facing guidance. Keep unexpected
            # exception details in the local log rather than the recording API.
            message = str(exc) if isinstance(exc, TranscriptionError) else ""
            if not message:
                message = f"轉錄未完成（{type(exc).__name__}）；請檢查模型、裝置及錄音格式，然後重試。"
            try:
                engine.set_recording_status(rec_id, "failed", message)
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

    def run_understanding(rec_id):
        try:
            understand_recording(engine, agents, rec_id)
            summaries_for_recording(engine.get_recording(rec_id))
            automatic_care()
        except NotFoundError:
            pass
        except Exception as error:
            LOG.warning("Memory job failed (%s)", type(error).__name__)
        finally:
            with job_lock:
                jobs.discard(rec_id)

    def enqueue_understanding(rec_id):
        rec = engine.get_recording(rec_id)
        if memory_mode != "deepseek":
            raise HTTPException(409, "DeepSeek memory processing is not enabled in this deployment.")
        if not rec["segments"]:
            raise HTTPException(422, "A completed transcript is required before DeepSeek processing.")
        with job_lock:
            if rec_id in jobs:
                return
            jobs.add(rec_id)
        try:
            engine.update_pipeline(rec_id, {"status": "pending", "stage": "pending", "error": None, "stages": []})
            executor.submit(run_understanding, rec_id)
        except Exception:
            with job_lock:
                jobs.discard(rec_id)
            raise

    def run_organization(rec_id):
        try:
            organize_recording(engine, agents, rec_id)
            summaries_for_recording(engine.get_recording(rec_id))
        except NotFoundError:
            pass
        except Exception as error:
            LOG.warning("Topic organization failed (%s)", type(error).__name__)
        finally:
            with job_lock:
                jobs.discard(rec_id)

    def enqueue_organization(rec_id):
        rec = engine.get_recording(rec_id)
        if memory_mode != "deepseek":
            raise HTTPException(409, "DeepSeek topic organization is not enabled in this deployment.")
        if not rec["segments"]:
            raise HTTPException(422, "A completed transcript is required before topic organization.")
        with job_lock:
            if rec_id in jobs:
                return
            jobs.add(rec_id)
        try:
            engine.update_topic_pipeline(rec_id, {"status": "pending", "stage": "consolidation", "error": None})
            executor.submit(run_organization, rec_id)
        except Exception:
            with job_lock:
                jobs.discard(rec_id)
            raise

    @app.get("/api/health")
    def health():
        status = asr.health()
        status["status"] = status.get("state", status.get("status", "ready"))
        # Local inference on a hosted server still requires uploading audio.
        status["local_only"] = deployment_mode == "local" and not status.get("external", False)
        return {"status": "ok", "version": "0.2.0", "asr": status, "analysis": llm.status(), "memory_layers": 5,
                "memory_pipeline": {**agents.status(), "mode": memory_mode},
                "deployment": {"mode": deployment_mode,
                               "audio_processing": "external_service" if status.get("external") else "server" if deployment_mode == "server" else "this_device",
                               "shared_workspace": deployment_mode == "server"}}

    @app.get("/api/state")
    def state(date: str | None = None):
        engine.cleanup_retention()
        return application_state(valid_date(date))

    @app.get("/api/recordings")
    def recordings():
        return engine.list_recordings()

    @app.get("/api/recordings/{rec_id}")
    def recording(rec_id: str):
        return engine.get_recording(rec_id)

    @app.patch("/api/recordings/{rec_id}")
    def revise_recording_time(rec_id: str):
        engine.get_recording(rec_id)
        raise HTTPException(409, "Uploading date is assigned automatically.")

    @app.patch("/api/recordings/{rec_id}/topics/{topic_id}")
    def revise_topic(rec_id: str, topic_id: str, body: TopicRevision):
        result = engine.update_topic(rec_id, topic_id, body.version, body.title, body.summary,
                                     memories=[memory.model_dump() for memory in body.memories] if body.memories is not None else None)
        if any(topic["id"] == topic_id and topic["version"] != body.version for topic in result["topics"]):
            engine.invalidate_card_summaries_for_recording(rec_id)
        summaries_for_recording(result)
        automatic_care()
        return result

    @app.post("/api/recordings")
    async def upload(file: UploadFile = File(...), recorded_at: str | None = Form(None), language: str = Form("en")):
        # Legacy clients may still send recorded_at; new imports always use
        # the server upload time and do not parse or trust that optional field.
        if language not in {"en", "auto"}:
            raise HTTPException(422, "本 MVP 提供英文或自動語言辨識。")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4"}:
            raise HTTPException(415, "請選擇 WAV、MP3、M4A、FLAC、OGG、WEBM 或 MP4 錄音。")
        dest = audio_dir / (uuid.uuid4().hex + suffix)
        hasher, size = hashlib.sha256(), 0
        maximum = int(os.getenv("HYA_MAX_UPLOAD_MB", "100")) * 1024 * 1024
        asr_limit = asr.health().get("max_upload_bytes")
        if isinstance(asr_limit, int) and asr_limit > 0:
            maximum = min(maximum, asr_limit)
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
            if asr.health().get("required_channels") == 1 and audio_info["channels"] != 1:
                raise HTTPException(422, "Tencent Cloud ASR requires mono audio; export a mono copy and retry.")
            rec = engine.create_uploaded_recording(file.filename or "錄音", str(dest), hasher.hexdigest())
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
        if memory_mode == "deepseek" and rec["segments"] and rec.get("memory_pipeline", {}).get("status") in {"failed", "stale"}:
            enqueue_understanding(rec_id)
            return engine.get_recording(rec_id)
        if rec["status"] not in {"failed", "queued"}:
            raise HTTPException(409, "這個錄音目前不需要重試。")
        if not rec.get("file_path") or not Path(rec["file_path"]).is_file():
            raise HTTPException(422, "原始錄音已不存在，請重新匯入。")
        enqueue(rec_id, rec.get("language") or "en")
        return engine.get_recording(rec_id)

    @app.post("/api/recordings/{rec_id}/understand")
    def understand(rec_id: str):
        enqueue_understanding(rec_id)
        return engine.get_recording(rec_id)

    @app.post("/api/recordings/{rec_id}/organize")
    def organize(rec_id: str):
        enqueue_organization(rec_id)
        return engine.get_recording(rec_id)

    @app.post("/api/recordings/{rec_id}/confirm")
    def confirm(rec_id: str, body: Confirmation):
        result = engine.confirm_recording(rec_id, body.segment_ids)
        summaries_for_recording(result)
        automatic_care()
        return result

    @app.delete("/api/recordings/{rec_id}")
    def remove_recording(rec_id: str):
        recording = engine.get_recording(rec_id)
        result = engine.delete_recording(rec_id)
        summaries_for_recording(recording)
        automatic_care()
        return result

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
        selected = valid_date(date)
        if memory_mode != "deepseek" or not query.strip():
            return engine.retrieve(query=query, date=selected, person=person)
        candidates = engine.retrieve(date=selected, person=person)[:100]
        versions = {m["id"]: m["version"] for m in candidates}
        ranked = agents.retrieve(query, candidates)
        current = {m["id"]: m for m in engine.retrieve(date=selected, person=person)
                   if m["id"] in versions and m["version"] == versions[m["id"]]}
        return [current[ident] for ident in ranked if ident in current]

    @app.patch("/api/memories/{memory_id}")
    def revise(memory_id: str, body: Revision):
        if not body.content.strip():
            raise HTTPException(422, "記憶內容不能是空白。")
        result = engine.revise_memory(memory_id, **body.model_dump())
        summaries_for_recording(engine.get_recording(result["recording_id"]))
        automatic_care()
        return result

    @app.delete("/api/memories/{memory_id}")
    def remove_memory(memory_id: str):
        source = engine.get_source(memory_id)
        result = engine.delete_memory(memory_id)
        summaries_for_recording(source["recording"])
        automatic_care()
        return result

    @app.patch("/api/settings")
    def preferences(body: dict):
        for field, low, high in (("frequency_minutes", 0, 1440), ("retention_days", 1, 3650)):
            if field in body and (type(body[field]) is not int or not low <= body[field] <= high):
                raise HTTPException(422, f"{field} 數值超出可用範圍。")
        result = engine.settings_patch(body)
        automatic_care()
        return result

    @app.post("/api/proactive/evaluate")
    def evaluate(body: DayRequest):
        return automatic_care(valid_date(body.date), explicit=True)

    @app.post("/api/proactive/comfort-now")
    def comfort_now(body: DayRequest):
        return display_support("comfort", valid_date(body.date))

    def display_support(kind, date):
        card = engine.request_support(kind, date)
        result = automatic_care(card["date"], explicit=True, requested_id=card["id"])
        if not result["cards"]:
            raise HTTPException(409, "The confirmed sources or support preferences changed. Refresh before requesting again.")
        return result

    @app.post("/api/proactive/display-now")
    def display_now(body: SupportRequest):
        return display_support(body.type, valid_date(body.date))

    @app.patch("/api/proactive/display-time")
    def display_time(body: DisplayTimeRevision):
        engine.update_display_time(body.type, body.display_at, body.expected_display_at)
        return application_state()

    @app.post("/api/proactive/{card_id}/feedback")
    def feedback(card_id: str, body: Feedback):
        result = engine.feedback(card_id, body.action)
        automatic_care()
        return result

    @app.post("/api/cards/{card_id}/confirm")
    def review_card(card_id: str, body: CardReview):
        result = engine.confirm_card(card_id, body.expected_versions, body.review_token)
        return application_state(result["date"])

    @app.post("/api/proactive/{card_id}/bedtime")
    def bedtime(card_id: str, body: BedtimeRequest):
        result = engine.schedule_bedtime(card_id, recording_time(body.due_at), body.expected_versions)
        return application_state(result["date"])

    @app.get("/api/datasets")
    def dataset_list():
        return {"datasets": datasets.list(), "purpose": "Reserved intake for later post-training; all imports await professional review and are not used for training or live reasoning.",
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
        try:
            return daily_summaries.state(valid_date(body.date), explicit=True)
        except SummaryGenerationError as error:
            raise HTTPException(error.status_code, str(error)) from None

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
            return {"message": "The frontend is not built yet. Run npm run build in the web directory, or use the dev server."}
    return app


app = create_app()
