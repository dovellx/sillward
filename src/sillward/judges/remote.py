"""HTTP calls shared by the llm and jev adapters.

The request body includes the task summary and tool state, because the judge
has to see them. Ledger rows do not. Failure messages name the class of
failure and leave out response bodies and API keys.
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Protocol

import httpx

from sillward.judges.base import JudgeFailure
from sillward.protocol import ProtocolFailure, Request, Verdict, check_verdict

_INSTRUCTIONS = (
    "Reply with one JSON object and no other text. "
    'Use the shape {"answer": string, "probabilities": {candidate: number}}. '
    "The answer must be one of the candidates. "
    "Include a probability for every candidate."
)


class PostingClient(Protocol):
    """The slice of ``httpx.Client`` these adapters call."""

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
        timeout: float,
    ) -> httpx.Response:
        """Send one request."""


def render_request(request: Request) -> str:
    """Serialize the question the model is asked to answer."""
    try:
        return json.dumps(
            {
                "point": request.point,
                "question": request.question.value,
                "candidates": list(request.candidates),
                "frame_summary": request.frame_summary,
                "state": dict(request.state),
            },
            ensure_ascii=False,
        )
    except TypeError as exc:
        raise JudgeFailure("protocol") from exc


def ask(
    client: PostingClient,
    *,
    url: str,
    api_key: str,
    payload: dict[str, object],
    adapter: str,
    request: Request,
    read_text,
) -> Verdict:
    """POST one JSON payload and parse a protocol verdict from the reply text."""
    body, elapsed_ms = _post_json(client, url=url, api_key=api_key, payload=payload)
    verdict = _verdict_from_text(read_text(body), adapter=adapter, elapsed_ms=elapsed_ms)
    try:
        check_verdict(request, verdict)
    except ProtocolFailure as exc:
        raise JudgeFailure("protocol") from exc
    return verdict


def chat_payload(model: str, request: Request) -> dict[str, object]:
    """Build a non-streaming chat-completions body."""
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _INSTRUCTIONS},
            {"role": "user", "content": render_request(request)},
        ],
    }


def responses_payload(model: str, request: Request) -> dict[str, object]:
    """Build a non-streaming Responses API body."""
    return {
        "model": model,
        "stream": False,
        "store": False,
        "instructions": _INSTRUCTIONS,
        "input": [{"role": "user", "content": render_request(request)}],
    }


def text_from_chat(body: dict[str, object]) -> str:
    """Read the assistant string from a chat-completions response."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise JudgeFailure("protocol")
    first = choices[0]
    if not isinstance(first, dict):
        raise JudgeFailure("protocol")
    message = first.get("message")
    if not isinstance(message, dict):
        raise JudgeFailure("protocol")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise JudgeFailure("protocol")
    return content


def text_from_responses(body: dict[str, object]) -> str:
    """Read concatenated output text from a Responses API response."""
    output = body.get("output")
    if not isinstance(output, list):
        raise JudgeFailure("protocol")
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "output_text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if not chunks:
        raise JudgeFailure("protocol")
    return "".join(chunks)


def _post_json(
    client: PostingClient,
    *,
    url: str,
    api_key: str,
    payload: dict[str, object],
) -> tuple[dict[str, object], float]:
    started = perf_counter()
    try:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            content=json.dumps(payload).encode("utf-8"),
            timeout=30.0,
        )
    except httpx.TimeoutException as exc:
        raise JudgeFailure("timeout") from exc
    elapsed_ms = (perf_counter() - started) * 1000.0
    if response.status_code != 200:
        raise JudgeFailure(f"http {response.status_code}")
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise JudgeFailure("protocol") from exc
    if not isinstance(body, dict):
        raise JudgeFailure("protocol")
    return body, elapsed_ms


def _verdict_from_text(text: str, *, adapter: str, elapsed_ms: float) -> Verdict:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudgeFailure("protocol") from exc
    if not isinstance(parsed, dict):
        raise JudgeFailure("protocol")
    answer = parsed.get("answer")
    probabilities = parsed.get("probabilities")
    if not isinstance(answer, str) or not isinstance(probabilities, dict):
        raise JudgeFailure("protocol")
    cleaned: dict[str, float] = {}
    for key, value in probabilities.items():
        if (
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, int | float)
        ):
            raise JudgeFailure("protocol")
        cleaned[key] = float(value)
    return Verdict(answer=answer, probabilities=cleaned, elapsed_ms=elapsed_ms, adapter=adapter)
