"""Check-in rules reject reported, negated, past and withdrawn assertions."""
import pytest

from hya.warning_signals import direct_distress_context, distress_topics, strong_distress


@pytest.mark.parametrize("text", [
    "I can't cope today.", "I can’t cope today.", "I'm completely overwhelmed.",
    "I feel hopeless.", "I am extremely overwhelmed by everything today.",
    "我撐不住了。", "我快崩溃了。", "我感到絕望。", "我感觉自己非常绝望。",
])
def test_strong_current_self_reports(text):
    assert strong_distress(text)


@pytest.mark.parametrize("text", [
    "I feel stressed.", "I feel a little anxious.", "I feel happy.",
    "My friend said I can't cope today.", "She feels hopeless.",
    'My friend said, "I can\'t cope today."', "朋友說『我撐不住了』。",
    "如果我撐不住了，我會找人幫忙。", "假如我感到绝望，我就会求助。",
    "If I can't cope, I will ask for help.", "For example, I feel hopeless.",
    "I do not feel hopeless.", "I don't think I feel hopeless.", "Not that I feel hopeless.",
    "我沒有感到絕望。", "我不認為我撐不住了。",
    "Yesterday I felt hopeless.", "I used to think I can't cope.", "以前我撐不住了。",
    "I feel hopeless. I feel better now.", "我撐不住了，但是我現在好多了。",
    "I never said I feel hopeless.", "I feel completely overwhelmed by joy.",
    "Do I feel hopeless? No.", "Maybe I feel hopeless.", "I am hopeless at chess.",
    "I cannot cope with spicy food.",
    "I feel hopeless?", "我感到絕望？", "I feel hopeless, my friend said.",
    "I feel hopeless, said my friend.",
    "I don’t think I feel hopeless.", "I didn’t say I feel hopeless.",
    "I don’t believe I’m completely overwhelmed.",
])
def test_ordinary_or_out_of_scope_words_are_not_strong_distress(text):
    assert not strong_distress(text)


@pytest.mark.parametrize("text,topic", [
    ("I feel stressed about work.", "stress"), ("I'm anxious today.", "stress"),
    ("I keep feeling sad.", "low_mood"), ("I feel lonely.", "social_withdrawal"),
    ("I have been avoiding everyone.", "social_withdrawal"),
    ("我最近壓力很大。", "stress"), ("我今天感到低落。", "low_mood"),
    ("我感到孤独。", "social_withdrawal"),
])
def test_repeatable_topics_use_explicit_self_reports(text, topic):
    assert topic in distress_topics(text)


@pytest.mark.parametrize("text", [
    "The speaker feels sad.", "She is anxious.", "I do not feel lonely.",
    "My friend said I feel stressed.", "An example is 'I feel lonely'.",
    "我朋友說我感到孤獨。", "我不觉得我感到孤独。", "I felt stressed last week.",
    "I feel stressed, but I can cope now.", "If I feel lonely I will call a friend.",
])
def test_topics_ignore_inferences_and_withdrawn_or_foreign_statements(text):
    assert distress_topics(text) == set()


@pytest.mark.parametrize("source,quote,expected", [
    ("朋友說『我撐不住了』。", "我撐不住了", False),
    ("My friend said I can't cope today.", "I can't cope today", False),
    ("If I can't cope I will ask.", "I can't cope", False),
    ("I don't think I feel hopeless.", "I feel hopeless", False),
    ("I feel hopeless. I feel better now.", "I feel hopeless", False),
    ("I feel hopeless today.", "I feel hopeless", True),
    ("我今天感到絕望。", "我今天感到絕望", True),
])
def test_quote_must_remain_in_its_original_scope(source, quote, expected):
    start = source.index(quote)
    assert direct_distress_context(source, start, start + len(quote)) is expected
