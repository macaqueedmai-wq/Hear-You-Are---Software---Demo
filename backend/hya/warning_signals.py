"""Conservative, source-word rules for an optional wellbeing check-in.

These recognise explicit self-reports, not diagnoses or predicted risk. The
engine supplies confirmed, dated source quotes; these rules never infer a
condition from a generated summary or a speaker's voice.
"""
from __future__ import annotations

import re


_QUOTED = re.compile(r'"[^"\n]*"|(?<!\w)\'[^\'\n]*\'(?!\w)|[“‘「『《][^”’」』》\n]*[”’」』》]')
_BOUNDARY = re.compile(r"[.!?;\n。！？；]")
_HYPOTHETICAL = re.compile(
    r"\b(?:if|unless|suppose|supposing|imagine|hypothetically|for example|an example|"
    r"as an example|in a story|in the story|pretend|fictional|would feel|would be|might feel)\b"
    r"|如果|假如|假設|假设|要是|例如|比如|舉例|举例|虛構|虚构|故事[裡里中]|扮演", re.I)
_REPORTED = re.compile(
    r"\b(?:(?:my|a|the) (?:friend|colleague|classmate|partner|mother|father|sister|brother|client|patient)|"
    r"he|she|they|someone|somebody|people)\b.{0,45}\b(?:said|says|told|saying|wrote|asked|feels?|is|was)\b"
    r"|(?:朋友|同事|同學|同学|媽媽|妈妈|爸爸|他|她|別人|别人|有人).{0,12}(?:說|说|表示|告訴|告诉|覺得|觉得|感到)", re.I)
_PAST = re.compile(
    r"\b(?:yesterday|last (?:night|week|month|year)|used to|back then|in the past|"
    r"when i was|i (?:felt|was|couldn't|could not))\b|昨天|昨晚|上星期|上個月|上个月|去年|以前|當時|当时|曾經|曾经", re.I)
_WITHDRAWN = re.compile(
    r"\b(?:i (?:never said|did not say|didn't say|do not (?:think|believe)|don't (?:think|believe))|not that|"
    r"it (?:is not|isn't|was not|wasn't) true|not true that|i was joking)\b"
    r"|我(?:沒說|没说|沒有說|没有说|不認為|不认为|不覺得|不觉得|不是說|不是说|在開玩笑|在开玩笑)|不是事實|不是事实", re.I)
_NOT_DISTRESS = re.compile(
    r"\boverwhelmed (?:by|with) (?:joy|happiness|gratitude|love|excitement)\b|"
    r"\bhopeless at\b|感動得|感动得|開心得|开心得", re.I)
_UNCERTAIN = re.compile(
    r"\b(?:maybe|perhaps|possibly|not sure|unsure|do i|am i|could i|can i|would i|might i)\b"
    r"|也許|也许|可能|是不是|是否|難道|难道", re.I)
_TRAILING_ATTRIBUTION = re.compile(
    r"[,，]\s*(?:(?:said|says|wrote|asked) (?:my friend|he|she|they)|"
    r"(?:my friend|he|she|they) (?:said|says|wrote|asked))\b|"
    r"[，,]\s*(?:朋友|他|她|別人|别人)(?:說|说|表示)", re.I)
_RESOLVED = re.compile(
    r"\bi (?:feel|am) (?:fine|calm|okay|ok|better)(?: now| today)?\b|"
    r"\bi (?:can cope|can manage|no longer feel (?:stressed|anxious|sad|lonely|hopeless|overwhelmed))\b|"
    r"(?:現在|现在|如今)我?(?:已經|已经)?(?:好多了|沒事|没事|不再|能應付|能应付|很平靜|很平静)|"
    r"我(?:現在|现在)(?:感覺|感觉|覺得|觉得)?(?:好多了|沒事|没事|能應付|能应付|很平靜|很平静)", re.I)


def _normal(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")


def direct_distress_context(text: str, start: int, end: int) -> bool:
    """Whether a matched quote belongs to a current direct assertion.

    Validate the full surrounding source, so extracting the substring 'I can't
    cope' from someone else's speech, a conditional, or a withdrawn statement
    cannot turn it into the user's own report.
    """
    if not isinstance(text, str) or not 0 <= start < end <= len(text):
        return False
    if any(match.start() < end and match.end() > start for match in _QUOTED.finditer(text)):
        return False
    # Smart apostrophes are common in transcripts. Keep source offsets intact
    # while applying the same negation rules to don't and don’t.
    text = _normal(text)
    left = max((match.end() for match in _BOUNDARY.finditer(text, 0, start)), default=0)
    right_match = _BOUNDARY.search(text, end)
    right = right_match.start() if right_match else len(text)
    sentence = text[left:right]
    if (right_match and right_match.group() in {"?", "？"}) or _TRAILING_ATTRIBUTION.search(sentence):
        return False
    # A new explicit self assertion after 'but' can end reported speech scope.
    prefix = text[left:start]
    breaks = list(re.finditer(r"\b(?:but|however)\b|但是|但我|不過|不过|可是", prefix, re.I))
    scoped_prefix = prefix[breaks[-1].end():] if breaks else prefix
    if (_HYPOTHETICAL.search(sentence) or _WITHDRAWN.search(sentence)
            or _NOT_DISTRESS.search(sentence) or _UNCERTAIN.search(sentence)):
        return False
    if _REPORTED.search(scoped_prefix) or _REPORTED.search(text[start:end]):
        return False
    # An explicit recent improvement supersedes the earlier distress assertion.
    if any(match.start() >= end for match in _RESOLVED.finditer(text)):
        return False
    scoped = scoped_prefix + text[start:end]
    if _PAST.search(scoped):
        return False
    return True


_SELF = r"\bi(?: (?:am|feel|have been feeling|have felt|keep feeling|still feel|am feeling)|'m)(?:\s+(?:very|really|so|extremely|completely|totally|deeply|quite|a little|still|constantly|always)){0,2}\s+"
_ZH_SELF = r"我(?:最近|今天|現在|现在|一直|還是|还是|仍然)?(?:感到|感覺|感觉|覺得|觉得|自己|都|很|好|非常|特別|特别|真的|有點|有点|十分|總是|总是|壓力|压力){0,3}"
_TOPICS = {
    "stress": re.compile(_SELF + r"(?:stressed|anxious|worried|overwhelmed|under pressure|panicky)\b|"
                         + _ZH_SELF + r"(?:焦慮|焦虑|緊張|紧张|擔心|担心|壓力很大|压力很大|壓力大|压力大|不知所措)", re.I),
    "low_mood": re.compile(_SELF + r"(?:sad|down|low|hopeless|empty|miserable)\b|"
                           + _ZH_SELF + r"(?:難過|难过|低落|傷心|伤心|絕望|绝望|空虛|空虚|沮喪|沮丧)", re.I),
    "social_withdrawal": re.compile(_SELF + r"(?:lonely|isolated)\b|"
                                    r"\bi (?:am avoiding|have been avoiding|keep avoiding) (?:everyone|people|my friends)\b|"
                                    r"\bi (?:do not|don't) want to (?:talk to|see|meet) (?:anyone|anybody|people|my friends)\b|"
                                    + _ZH_SELF + r"(?:孤單|孤单|孤獨|孤独|寂寞)|"
                                    r"我(?:最近|今天|一直)?(?:不想見|不想见|不想跟|不想和|躲著|躲着|避開|避开)(?:任何人|別人|别人|朋友|大家)", re.I),
}
_STRONG = re.compile(
    r"\bi (?:cannot|can't|can not) (?:cope(?:\s+with\s+(?:everything|all (?:this|the)|"
    r"(?:this|the|my) (?:stress|pressure|workload|work|life|situation)|work|school|life)\b|(?!\s+with\b))"
    r"|get through the day|manage (?:basic|everyday|daily) (?:things|tasks))\b|"
    r"\bi(?: (?:feel|am|am feeling)|'m) (?:(?:completely|totally|extremely) overwhelmed|(?:so |really |utterly )?hopeless)\b|"
    r"我(?:真的|已經|已经|現在|现在|感覺|感觉|覺得|觉得)?(?:撐不住|撑不住|快崩潰|快崩溃|無法應付|无法应付|應付不來|应付不来)|"
    + _ZH_SELF + r"(?:絕望|绝望)", re.I)


def _matches(text: str, pattern: re.Pattern) -> bool:
    normalized = _normal(text)
    # Normalisation preserves offsets for the supported English/Chinese rules.
    return any(direct_distress_context(text, match.start(), match.end())
               for match in pattern.finditer(normalized))


def strong_distress(text: str) -> bool:
    """Recognise a current explicit strong self-report; no severity inference."""
    return isinstance(text, str) and _matches(text, _STRONG)


def distress_topics(text: str) -> set[str]:
    """Topics for counting distinct source days, never number of atoms."""
    if not isinstance(text, str):
        return set()
    return {topic for topic, pattern in _TOPICS.items() if _matches(text, pattern)}
