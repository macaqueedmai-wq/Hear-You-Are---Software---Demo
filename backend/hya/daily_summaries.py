"""Nonblocking daily prose generation against complete, versioned source snapshots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import threading

from .engine import VersionConflict

LOG = logging.getLogger("hya")


class SummaryGenerationError(ValueError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class DailySummaryCoordinator:
    def __init__(self, engine, analyzer, *, automatic: bool):
        self.engine, self.analyzer, self.automatic = engine, analyzer, automatic
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hya-daily")
        self.jobs, self.lock = {}, threading.Lock()

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _snapshot(self, date):
        state = self.engine.state(date)
        selected = {ident for card in state["cards"] for ident in card["memory_ids"]}
        memories = [memory for memory in state["memories"] if memory["id"] in selected]
        signature = (state["date"], tuple(sorted((memory["id"], memory["version"], memory["category"])
                                                   for memory in memories)))
        return state, memories, signature

    @staticmethod
    def _ready(state):
        return bool(state["cards"]) and all(card.get("analysis_mode") == "llm" for card in state["cards"])

    @staticmethod
    def _with_status(state, status, message):
        return {**state, "summary_generation": {"status": status, "message": message}}

    @staticmethod
    def _source_context(state, memories):
        records = {record["id"]: record for record in state["recordings"]}
        context = []
        for memory in memories:
            recording = records.get(memory["recording_id"], {})
            source = next((source for source in recording.get("segments", [])
                           if source["id"] == memory["segment_id"]), {})
            context.append((memory["id"], recording.get("status"), source.get("subject_id"),
                            source.get("confirmed") is True))
        return tuple(sorted(context))

    @staticmethod
    def _sources_ready(context):
        return all(status in {"ready", "completed"} and subject == "user" and confirmed
                   for _, status, subject, confirmed in context)

    def _generate(self, date, memories, signature):
        conflict_context = None
        try:
            current, _, current_signature = self._snapshot(date)
            if current_signature != signature:
                raise VersionConflict("Daily-summary sources changed before generation.")
            conflict_context = self._source_context(current, memories)
            if not self._sources_ready(conflict_context):
                raise VersionConflict("Daily-summary identity or source status changed.")
            conflict_context = None
            # No engine transaction or coordinator lock is held during HTTP.
            outputs = self.analyzer.summarize(memories)
            categories = {memory["category"] for memory in memories}
            if (not isinstance(outputs, list) or len(outputs) != len(categories)
                    or any(not isinstance(output, dict) for output in outputs)
                    or {output.get("category") for output in outputs} != categories):
                raise ValueError("The model did not return every eligible daily category.")
            self.engine.apply_card_summaries(date, outputs,
                {memory["id"]: memory["version"] for memory in memories},
                expected_categories={memory["id"]: memory["category"] for memory in memories})
            return {"status": "ready", "message": "Daily summaries are ready."}
        except VersionConflict:
            if conflict_context is None:
                latest, _, _ = self._snapshot(date)
                conflict_context = self._source_context(latest, memories)
            return {"status": "failed", "status_code": 409,
                    "source_context": conflict_context,
                    "message": "Sources changed during daily summary generation. Current sources will be checked again."}
        except ValueError:
            return {"status": "failed", "status_code": 422,
                    "message": "The model returned invalid or incomplete daily summaries. Retry generation."}
        except Exception as error:
            LOG.warning("Daily summary generation failed (%s)", type(error).__name__)
            return {"status": "failed", "status_code": 502,
                    "message": "Daily summary generation failed. Retry generation."}

    def state(self, date=None, *, explicit=False):
        snapshot, memories, signature = self._snapshot(date)
        if not memories:
            return self._with_status(snapshot, "no-sources", "No dated, identity-confirmed memories are ready for daily summaries.")
        if not self.automatic and not explicit:
            return self._with_status(snapshot, "ready", "Local daily summaries are ready.")
        if self._ready(snapshot):
            return self._with_status(snapshot, "ready", "Daily summaries are ready.")
        if not self.analyzer.status().get("configured"):
            message = "Daily summary generation is not configured. Check the text-model settings."
            if explicit:
                raise SummaryGenerationError(message, 422)
            return self._with_status(snapshot, "unconfigured", message)
        with self.lock:
            future = self.jobs.get(signature)
            if future is not None and future.done():
                outcome = future.result()
                if outcome.get("status_code") == 409:
                    context = self._source_context(snapshot, memories)
                    if context != outcome.get("source_context") and self._sources_ready(context):
                        # Retry a source conflict only after observed eligibility
                        # changes; provider/schema failures remain memoized.
                        future = None
            if future is not None and future.done() and future.result()["status"] == "ready":
                # A topic-only correction may invalidate prose without changing
                # any atomic version. Recheck the cache before reusing success.
                latest, latest_memories, latest_signature = self._snapshot(snapshot["date"])
                if latest_signature != signature:
                    return self._with_status(latest, "queued", "Sources changed; daily summaries will be refreshed.")
                if self._ready(latest):
                    return self._with_status(latest, "ready", "Daily summaries are ready.")
                snapshot, memories = latest, latest_memories
                future = None
            if future is None or explicit and future.done():
                future = self.executor.submit(self._generate, snapshot["date"], memories, signature)
                self.jobs[signature] = future
            if len(self.jobs) > 128:
                for previous in list(self.jobs):
                    if previous != signature and self.jobs[previous].done():
                        self.jobs.pop(previous)
                        if len(self.jobs) <= 128:
                            break
        if explicit or future.done():
            outcome = future.result()
            latest, _, latest_signature = self._snapshot(snapshot["date"])
            if outcome["status"] == "ready" and latest_signature == signature and self._ready(latest):
                return self._with_status(latest, "ready", outcome["message"])
            if explicit and outcome["status"] == "failed":
                raise SummaryGenerationError(outcome["message"], outcome["status_code"])
            if latest_signature != signature or outcome["status"] == "ready":
                return self._with_status(latest, "queued", "Sources changed; daily summaries will be refreshed.")
            return self._with_status(latest, "failed", outcome["message"])
        return self._with_status(snapshot, "running" if future.running() else "queued",
                                 "Daily summaries are being generated automatically.")
