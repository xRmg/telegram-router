from __future__ import annotations

from typing import Any

from .models import ParameterSpec

DECISIONS_PATH = "/decisions"
NO_MATCH_OPTION = "none"
ABSENT_OPTION = "__absent__"
MAX_NGRAM = 3
CANDIDATE_CAP = 60
_PUNCTUATION = ",.?!;:\"'()"


def decisions_base_url(llm_base_url: str) -> str:
    trimmed = llm_base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        trimmed = trimmed[: -len("/v1")]
    return f"{trimmed}/alpha"


def build_candidates(text: str) -> tuple[list[str], bool]:
    tokens = [token.strip(_PUNCTUATION) for token in text.split()]
    tokens = [token for token in tokens if token]
    seen: dict[str, str] = {}
    for size in range(1, MAX_NGRAM + 1):
        for start in range(len(tokens) - size + 1):
            span = " ".join(tokens[start : start + size])
            seen.setdefault(span.casefold(), span)
    candidates = list(seen.values())
    return candidates[:CANDIDATE_CAP], len(candidates) > CANDIDATE_CAP


def request_payload(model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    return {"model": model, "state": state, "questions": questions}


def capability_question(criteria: dict[str, str]) -> dict[str, Any]:
    options = dict(criteria)
    options[NO_MATCH_OPTION] = "Nothing above matches the user's message."
    return {
        "type": "choice",
        "instructions": (
            "Which capability best matches the user's message? "
            f"Choose '{NO_MATCH_OPTION}' if nothing fits."
        ),
        "criteria": options,
    }


def parameter_question(
    tool_name: str,
    name: str,
    spec: ParameterSpec,
    options: list[str],
) -> dict[str, Any]:
    detail = f" {spec.description}." if spec.description else ""
    if spec.enum is not None:
        lead = f"Which option is the value for the '{name}' parameter of {tool_name}?"
    else:
        lead = (
            f"Which candidate span is the value for the '{name}' parameter "
            f"of {tool_name}?"
        )
    criteria: dict[str, Any] = {option: None for option in options}
    criteria[ABSENT_OPTION] = None
    return {
        "type": "choice",
        "instructions": (
            f"{lead}{detail} "
            f"Choose '{ABSENT_OPTION}' if the message does not state it."
        ),
        "criteria": criteria,
    }


def choice_answer(data: dict[str, Any], key: str) -> tuple[str | None, float]:
    answer = (data.get("answers") or {}).get(key) or {}
    choice = answer.get("choice")
    if not isinstance(choice, str):
        return None, 0.0
    try:
        confidence = float(answer.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    return choice, min(max(confidence, 0.0), 1.0)
