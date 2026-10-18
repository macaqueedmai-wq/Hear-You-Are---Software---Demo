"""Provider-stub tests for real, source-grounded Warning wording."""
from __future__ import annotations

import json

import httpx
import pytest

from hya import llm as module
from hya.llm import TextAnalyzer


@pytest.fixture(autouse=True)
def isolated_warning_provider(monkeypatch, isolated_provider_mode):
    monkeypatch.setenv("HYA_MEMORY_MODE", "deepseek")
    monkeypatch.setenv("HYA_ALLOW_EXTERNAL_LLM", "true")
    monkeypatch.setenv("HYA_LLM_BASE_URL", "https://warning-provider.example.test")
    monkeypatch.setenv("HYA_LLM_MODEL", "fictional-test-model")
    monkeypatch.setenv("HYA_LLM_API_KEY", "fictional-warning-test-key")


def respond(monkeypatch, output):
    requests = []
    client = httpx.Client

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]})

    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs))
    return requests


def warning(*, basis="acute_distress", is_demo=False):
    dates = ["2026-10-18"] if basis == "acute_distress" else ["2026-10-14", "2026-10-16", "2026-10-18"]
    return {"type": "warning", "support_basis": basis, "is_demo": is_demo, "demo_only": is_demo,
            "condition_reason": "The user's explicit original words matched the app check-in rule.",
            "summary": "An optional check-in based on the source words.",
            "warning_trigger": {"basis": basis, "topics": ["overwhelmed"], "dates": dates,
                                "day_count": len(dates), "window_days": 7,
                                "evidence_quotes": [{"memory_id": "warning-source-1", "text": "I feel completely overwhelmed."}]}}


def memory(content="I feel completely overwhelmed."):
    return {"id": "warning-source-1", "content": content, "event_time": "2026-10-18T12:00:00+08:00",
            "source_quote": content, "is_demo": False}


@pytest.mark.parametrize("basis", ["acute_distress", "repeated_distress"])
def test_real_warning_receives_rule_dates_original_quotes_and_language_instruction(monkeypatch, basis):
    output = {"title": "A moment to check in", "summary": (
        "You said you felt completely overwhelmed on 2026-10-18, which matched the app's check-in rule. "
        "If it feels helpful, take a short pause.")}
    requests = respond(monkeypatch, output)
    assert TextAnalyzer().care_text(warning(basis=basis), [memory()]) == output
    assert len(requests) == 1
    prompt = requests[0]["messages"][0]["content"]
    assert "same language as the original evidence quotes" in prompt
    assert "not medical triage" in prompt
    assert "never a clinical threshold" in prompt
    assert "one concrete, gentle, optional next step" in prompt
    assert "must both explicitly say fictional" not in prompt
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert payload["is_demo"] is False
    assert payload["support_basis"] == basis
    assert payload["output_language"] == "English"
    assert "title and summary MUST be entirely English" in prompt
    assert payload["warning_trigger"] == warning(basis=basis)["warning_trigger"]
    assert payload["source"][0]["source_quote"] == memory()["source_quote"]
    assert payload["source"][0]["event_time"] == memory()["event_time"]


def test_real_chinese_warning_preserves_script_and_actual_evidence(monkeypatch):
    quote = "我覺得完全撐不住了。"
    card = warning()
    card["warning_trigger"]["evidence_quotes"] = [{"memory_id": "warning-source-1", "text": quote}]
    output = {"title": "給自己一點空間", "summary": (
        "你在2026-10-18說「我覺得完全撐不住了。」，這符合應用程式的關懷提醒規則。"
        "如果願意，可以先停下來休息一會。")}
    requests = respond(monkeypatch, output)
    assert TextAnalyzer().care_text(card, [memory(quote)]) == output
    assert "Preserve Chinese script" in requests[0]["messages"][0]["content"]
    assert "title and summary MUST be entirely Chinese" in requests[0]["messages"][0]["content"]
    assert json.loads(requests[0]["messages"][1]["content"])["output_language"] == "Chinese"
    assert json.loads(requests[0]["messages"][1]["content"])["warning_trigger"]["evidence_quotes"] == [
        {"memory_id": "warning-source-1", "text": quote}]


@pytest.mark.parametrize("demo_location", ["is_demo", "demo_only", "memory"])
def test_fictional_legacy_and_source_flags_keep_warning_explicitly_fictional(monkeypatch, demo_location):
    output = {"title": "Fictional warning", "summary": "Fictional example only; this is a gentle test check-in."}
    requests = respond(monkeypatch, output)
    card, sources = warning(), [memory()]
    if demo_location == "memory":
        sources[0]["is_demo"] = True
    else:
        card[demo_location] = True
    assert TextAnalyzer().care_text(card, sources) == output
    assert "This warning is a fictional demo only" in requests[0]["messages"][0]["content"]
    assert json.loads(requests[0]["messages"][1]["content"])["is_demo"] is True


@pytest.mark.parametrize("output", [
    {"title": "Fictional warning", "summary": "Take a short pause."},
    {"title": "A small check-in", "summary": "This is a simulated support experience."},
    {"title": "虛構警示", "summary": "可以休息一下。"},
])
def test_real_warning_cannot_be_mislabelled_as_fiction(monkeypatch, output):
    respond(monkeypatch, output)
    with pytest.raises(ValueError, match="Real support must not be labelled"):
        TextAnalyzer().care_text(warning(), [memory()])


@pytest.mark.parametrize("text", [
    "You have depression. Take a pause.",
    "You are at high risk. Talk to someone.",
    "You will harm yourself. Take a pause.",
    "This means depression. Talk to someone.",
    "Call 999 now.",
    "Contact emergency services immediately.",
    "你患有抑鬱症，可以休息一下。",
    "你將會自殺，請找人陪伴。",
    "立即撥打999。",
    "撥打熱線：12345678。",
])
def test_real_warning_rejects_fabricated_diagnosis_prediction_urgency_and_phone_numbers(monkeypatch, text):
    respond(monkeypatch, {"title": "A check-in", "summary": text})
    with pytest.raises(ValueError, match="must not invent diagnosis"):
        TextAnalyzer().care_text(warning(), [memory()])


def test_warning_can_quote_user_language_without_claiming_diagnosis(monkeypatch):
    quote = "You have depression."
    card = warning()
    card["warning_trigger"]["evidence_quotes"] = [{"memory_id": "warning-source-1", "text": quote}]
    output = {"title": "A check-in", "summary": 'You reported hearing "You have depression." If helpful, take a short pause.'}
    respond(monkeypatch, output)
    assert TextAnalyzer().care_text(card, [memory(quote)]) == output


def test_warning_rejects_invented_source_date(monkeypatch):
    respond(monkeypatch, {"title": "A check-in", "summary": "You described this on 2026-10-10. Take a short pause."})
    with pytest.raises(ValueError, match="only the source dates"):
        TextAnalyzer().care_text(warning(), [memory()])


def test_real_warning_requires_engine_basis_before_any_provider_request(monkeypatch):
    requests = respond(monkeypatch, {"title": "A check-in", "summary": "Take a short pause."})
    card = warning(basis="fictional_warning")
    with pytest.raises(ValueError, match="explicit, source-validated"):
        TextAnalyzer().care_text(card, [memory()])
    assert requests == []


@pytest.mark.parametrize("output", [
    {"title": "今天先緩一緩", "summary": "If helpful, take a short pause."},
    {"title": "A check-in", "summary": "如果願意，可以先休息一下。"},
    {"title": "今天先缓一缓", "summary": "如果愿意，可以先休息一下。"},
])
def test_english_warning_rejects_chinese_title_or_summary(monkeypatch, output):
    respond(monkeypatch, output)
    with pytest.raises(ValueError, match="language of its current source evidence"):
        TextAnalyzer().care_text(warning(), [memory()])


@pytest.mark.parametrize("output", [
    {"title": "A check-in", "summary": "如果願意，可以先休息一下。"},
    {"title": "給自己一點空間", "summary": "If helpful, take a short pause."},
])
def test_chinese_warning_requires_chinese_in_both_title_and_summary(monkeypatch, output):
    card = warning()
    card["warning_trigger"]["evidence_quotes"] = [{"memory_id": "warning-source-1", "text": "我覺得撐不住了。"}]
    respond(monkeypatch, output)
    with pytest.raises(ValueError, match="language of its current source evidence"):
        TextAnalyzer().care_text(card, [memory("我覺得撐不住了。")])


@pytest.mark.parametrize("quote,context,output,expected_language", [
    ("I feel completely overwhelmed.", "我覺得撐不住了。",
     {"title": "A check-in", "summary": "If helpful, take a short pause."}, "English"),
    ("我覺得撐不住了。", "I feel completely overwhelmed.",
     {"title": "給自己一點空間", "summary": "如果願意，可以先休息一下。"}, "Chinese"),
])
def test_warning_language_uses_authoritative_quote_over_draft_or_stale_source(monkeypatch, quote, context, output, expected_language):
    card = warning()
    card["summary"] = context
    card["warning_trigger"]["evidence_quotes"] = [{"memory_id": "warning-source-1", "text": quote}]
    requests = respond(monkeypatch, output)
    assert TextAnalyzer().care_text(card, [memory(context)]) == output
    assert json.loads(requests[0]["messages"][1]["content"])["output_language"] == expected_language
