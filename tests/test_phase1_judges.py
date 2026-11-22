"""Phase 1 judge adapters: recorded HTTP, and the error paths around it."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

API_KEY = "sk-super-secret-sentinel"
FILE_CONTENT = "file-body-secret-sentinel"


def _request(point: str, *, summary: str = "user-original-do-not-touch-the-database"):
    from sillward.protocol import Question, Request

    return Request(
        point=point,
        question=Question.CHOICE,
        candidates=("ship", "hold"),
        frame_summary=summary,
        state={"parameters": "bash-param-rm-rf-sentinel", "file_content": FILE_CONTENT},
    )


PROTOCOL_TEXT = '{"answer":"ship","probabilities":{"ship":0.81,"hold":0.19}}'
RECORDED_RESPONSES = {
    "id": "resp_recorded",
    "object": "response",
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": PROTOCOL_TEXT}],
        }
    ],
}
RECORDED_CHAT = {
    "id": "chatcmpl-recorded",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": PROTOCOL_TEXT},
            "finish_reason": "stop",
        }
    ],
}


def _client(handler) -> tuple[httpx.Client, dict[str, object]]:
    seen: dict[str, object] = {}

    def _record(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(_record), trust_env=False), seen


def test_llm_judge_uses_the_model_tau_has_logged_in(tmp_path: Path) -> None:
    """A hardcoded model would ignore the provider and model tau saved."""
    from tau_coding.credentials import FileCredentialStore
    from tau_coding.paths import TauPaths
    from tau_coding.provider_config import save_default_provider_model

    from sillward.judges.llm import LlmJudge

    home = tmp_path / "tau"
    home.mkdir()
    paths = TauPaths(home=home)
    save_default_provider_model(provider_name="openai", model="gpt-4o", paths=paths)
    FileCredentialStore(home / "credentials.json").set("openai", "sk-logged-in")
    client, seen = _client(lambda _request: httpx.Response(200, json=RECORDED_RESPONSES))

    verdict = LlmJudge.from_tau(home=home, environ={}, client=client).judge(
        _request("task.frame", summary="short-summary")
    )

    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert body["model"] == "gpt-4o"
    assert body["stream"] is False
    assert seen["authorization"] == "Bearer sk-logged-in"
    assert "short-summary" in json.dumps(body)
    assert verdict.answer == "ship"
    assert dict(verdict.probabilities) == {"ship": 0.81, "hold": 0.19}
    assert verdict.adapter == "llm"
    assert verdict.elapsed_ms >= 0
    client.close()


def test_environment_api_key_is_sent_ahead_of_the_stored_credential(tmp_path: Path) -> None:
    """Preferring the credential file would send sk-stored while the env key is set."""
    from tau_coding.credentials import FileCredentialStore

    from sillward.judges.llm import LlmJudge

    home = tmp_path / "tau"
    home.mkdir()
    FileCredentialStore(home / "credentials.json").set("openai", "sk-stored")
    client, seen = _client(lambda _request: httpx.Response(200, json=RECORDED_RESPONSES))

    LlmJudge.from_tau(home=home, environ={"OPENAI_API_KEY": "sk-from-env"}, client=client).judge(
        _request("task.frame")
    )

    assert seen["authorization"] == "Bearer sk-from-env"
    client.close()


def test_llm_judge_posts_chat_completions_for_that_api() -> None:
    """Sending /responses for an openai-completions endpoint would miss the model."""
    from sillward.judges.llm import LlmJudge, ModelEndpoint

    client, seen = _client(lambda _request: httpx.Response(200, json=RECORDED_CHAT))
    verdict = LlmJudge(
        endpoint=ModelEndpoint(
            model="local-model",
            base_url="https://gateway.test/v1",
            api="openai-completions",
            api_key="sk-local",
        ),
        client=client,
    ).judge(_request("task.frame"))

    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["url"] == "https://gateway.test/v1/chat/completions"
    assert body["model"] == "local-model"
    assert verdict.answer == "ship"
    assert verdict.adapter == "llm"
    client.close()


def _http_error(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, text=f"upstream {API_KEY} {FILE_CONTENT}")


def _timeout(_request: httpx.Request) -> httpx.Response:
    raise httpx.TimeoutException("slow")


def _prose(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": f"sure {FILE_CONTENT}"}],
                }
            ]
        },
    )


@pytest.mark.parametrize(
    "handler",
    [_http_error, _timeout, _prose],
    ids=["http", "timeout", "protocol"],
)
def test_llm_judge_failures_hide_the_body_and_do_not_return_a_verdict(handler) -> None:
    """A 5xx page, a timeout, or prose instead of protocol JSON is a judge failure."""
    from sillward.judges.base import JudgeFailure
    from sillward.judges.llm import LlmJudge, ModelEndpoint

    client, _seen = _client(handler)
    judge = LlmJudge(
        endpoint=ModelEndpoint(
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            api="openai-responses",
            api_key=API_KEY,
        ),
        client=client,
    )

    with pytest.raises(JudgeFailure) as caught:
        judge.judge(_request("tool.approval"))

    message = str(caught.value)
    assert API_KEY not in message
    assert FILE_CONTENT not in message
    client.close()


def test_missing_tau_credentials_fail_before_any_http_call(tmp_path: Path) -> None:
    """A missing login must not post to the provider."""
    from sillward.judges.base import JudgeFailure
    from sillward.judges.llm import LlmJudge

    home = tmp_path / "tau"
    home.mkdir()

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"posted to {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(_refuse), trust_env=False)
    with pytest.raises(JudgeFailure):
        LlmJudge.from_tau(home=home, environ={}, client=client).judge(_request("task.frame"))
    client.close()


def test_unsupported_tau_provider_fails_before_any_http_call(tmp_path: Path) -> None:
    """Anthropic's API is not called through the OpenAI HTTP paths."""
    from dataclasses import replace

    from tau_coding.paths import TauPaths
    from tau_coding.provider_config import load_provider_settings, save_provider_settings

    from sillward.judges.base import JudgeFailure
    from sillward.judges.llm import LlmJudge

    home = tmp_path / "tau"
    home.mkdir()
    paths = TauPaths(home=home)
    settings = load_provider_settings(paths)
    save_provider_settings(replace(settings, default_provider="anthropic"), paths)

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"posted to {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(_refuse), trust_env=False)
    with pytest.raises(JudgeFailure):
        LlmJudge.from_tau(
            home=home,
            environ={"ANTHROPIC_API_KEY": "sk-anthropic"},
            client=client,
        ).judge(_request("task.frame"))
    client.close()


def test_llm_failure_blocks_a_tool_point_without_writing_the_key(tmp_path: Path) -> None:
    """The kernel's block row must not keep the provider key or the HTTP body."""
    from sillward.config import load_config
    from sillward.judges.llm import LlmJudge, ModelEndpoint
    from sillward.kernel.runner import Kernel

    project = tmp_path / "project"
    project.mkdir()
    client, _seen = _client(
        lambda _request: httpx.Response(503, text=f"down {API_KEY} {FILE_CONTENT}")
    )
    decision = Kernel(
        config=load_config(home=tmp_path / "home", project=project, environ={}),
        judge=LlmJudge(
            endpoint=ModelEndpoint(
                model="gpt-4o",
                base_url="https://api.openai.com/v1",
                api="openai-responses",
                api_key=API_KEY,
            ),
            client=client,
        ),
        project=project,
    ).consult(_request("tool.approval"))

    text = (project / ".sillward" / "ledger.jsonl").read_text(encoding="utf-8")
    assert decision.action == "block"
    assert json.loads(text)["action"] == "block"
    assert API_KEY not in text
    assert FILE_CONTENT not in text
    client.close()


def test_jev_judge_reads_a_recorded_chat_completion() -> None:
    """The jev adapter posts jev_model and parses the protocol object from the reply."""
    from sillward.judges.jev import JevJudge

    client, seen = _client(lambda _request: httpx.Response(200, json=RECORDED_CHAT))
    verdict = JevJudge(
        model="system-one",
        api_key="sk-jev",
        base_url="https://judge.test/v1",
        client=client,
    ).judge(_request("swarm.routing", summary="short-summary"))

    body = seen["body"]
    assert isinstance(body, dict)
    assert seen["url"] == "https://judge.test/v1/chat/completions"
    assert body["model"] == "system-one"
    assert seen["authorization"] == "Bearer sk-jev"
    assert "short-summary" in json.dumps(body)
    assert verdict.answer == "ship"
    assert dict(verdict.probabilities) == {"ship": 0.81, "hold": 0.19}
    assert verdict.adapter == "jev"
    client.close()


def test_jev_without_an_endpoint_fails_before_http() -> None:
    """No host is named in the plan, so a jev judge with no base URL must not post."""
    from sillward.judges.base import JudgeFailure
    from sillward.judges.jev import JevJudge

    def _refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"posted to {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(_refuse), trust_env=False)
    with pytest.raises(JudgeFailure) as caught:
        JevJudge(model="system-one", api_key="sk-jev", base_url=None, client=client).judge(
            _request("swarm.routing")
        )
    assert "sk-jev" not in str(caught.value)
    client.close()


def test_judge_selection_follows_config_and_falls_back_without_a_key(tmp_path: Path) -> None:
    """A present TYPESAFE_API_KEY must not override judge llm, and jev without it uses llm."""
    from sillward.config import load_config
    from sillward.judges.factory import select_judge

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    tau_home = tmp_path / "tau"
    tau_home.mkdir()
    destination = home / ".sillward"
    destination.mkdir(parents=True)
    config_path = destination / "config.json"
    client = httpx.Client(trust_env=False)

    config_path.write_text(json.dumps({"judge": "llm"}), encoding="utf-8")
    llm_config = load_config(
        home=home,
        project=project,
        environ={"TYPESAFE_API_KEY": "sk-jev"},
    )
    selected_llm = select_judge(
        llm_config,
        environ={"TYPESAFE_API_KEY": "sk-jev"},
        tau_home=tau_home,
        client=client,
    )
    assert selected_llm.name == "llm"

    config_path.write_text(
        json.dumps({"judge": "jev", "jev_model": "system-one"}),
        encoding="utf-8",
    )
    jev_config = load_config(home=home, project=project, environ={})
    assert select_judge(jev_config, environ={}, tau_home=tau_home, client=client).name == "llm"
    selected = select_judge(
        jev_config,
        environ={"TYPESAFE_API_KEY": "sk-jev"},
        tau_home=tau_home,
        client=client,
    )
    assert selected.name == "jev"
    client.close()
