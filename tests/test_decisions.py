import pytest

from telegram_proxy.decisions import (
    ABSENT_OPTION,
    CANDIDATE_CAP,
    NO_MATCH_OPTION,
    build_candidates,
    capability_question,
    choice_answer,
    decisions_base_url,
    parameter_question,
)
from telegram_proxy.models import CommandSpec, ParameterSpec


def test_decisions_base_url_strips_v1():
    assert decisions_base_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/alpha"
    assert decisions_base_url("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/alpha"
    assert decisions_base_url("https://host/api") == "https://host/api/alpha"


def test_build_candidates_ngrams_and_punctuation():
    candidates, over_cap = build_candidates("lights on, kitchen!")
    assert not over_cap
    assert "lights" in candidates
    assert "kitchen" in candidates
    assert "lights on" in candidates
    assert "lights on kitchen" in candidates


def test_build_candidates_dedupes_case_insensitively():
    candidates, _ = build_candidates("Kitchen kitchen")
    assert [c for c in candidates if c.casefold() == "kitchen"] == ["Kitchen"]


def test_build_candidates_reports_cap():
    candidates, over_cap = build_candidates(" ".join(f"w{i}" for i in range(80)))
    assert over_cap
    assert len(candidates) == CANDIDATE_CAP


def test_capability_question_adds_no_match_option():
    question = capability_question({"svc.cmd": "does a thing"})
    assert question["type"] == "choice"
    assert question["criteria"]["svc.cmd"] == "does a thing"
    assert NO_MATCH_OPTION in question["criteria"]


def test_parameter_question_enum_uses_enum_options():
    spec = ParameterSpec(type="string", description="Room name", enum=["kitchen", "attic"])
    question = parameter_question("ha.lights_on", "room", spec, spec.enum)
    assert set(question["criteria"]) == {"kitchen", "attic", ABSENT_OPTION}
    assert "Which option" in question["instructions"]
    assert "Room name" in question["instructions"]


def test_parameter_question_span_uses_candidates():
    spec = ParameterSpec(type="string", description="Room name", extract="span")
    question = parameter_question("ha.lights_on", "room", spec, ["the kitchen"])
    assert set(question["criteria"]) == {"the kitchen", ABSENT_OPTION}
    assert "candidate span" in question["instructions"]


def test_choice_answer_parses_and_clamps():
    data = {"answers": {"room": {"choice": "kitchen", "confidence": 1.4}}}
    assert choice_answer(data, "room") == ("kitchen", 1.0)


def test_choice_answer_missing_key():
    assert choice_answer({"answers": {}}, "room") == (None, 0.0)
    assert choice_answer({}, "room") == (None, 0.0)


def test_choice_answer_bad_confidence_is_zero():
    data = {"answers": {"room": {"choice": "kitchen", "confidence": "nope"}}}
    assert choice_answer(data, "room") == ("kitchen", 0.0)


def test_parameter_spec_decision_extractable():
    assert not ParameterSpec().decision_extractable
    assert ParameterSpec(extract="span").decision_extractable
    assert ParameterSpec(enum=["a", "b"]).decision_extractable


def test_enum_must_not_be_empty():
    with pytest.raises(ValueError):
        ParameterSpec(enum=[])


def test_enum_must_be_unique():
    with pytest.raises(ValueError):
        ParameterSpec(enum=["kitchen", "Kitchen"])


def test_command_decision_routable():
    no_params = CommandSpec(name="lock_door")
    assert no_params.decision_routable

    span_only = CommandSpec(
        name="lights_on",
        parameters={"room": ParameterSpec(extract="span", required=True)},
    )
    assert span_only.decision_routable

    mixed = CommandSpec(
        name="add",
        parameters={
            "title": ParameterSpec(extract="span", required=True),
            "date": ParameterSpec(required=True),
        },
    )
    assert not mixed.decision_routable
