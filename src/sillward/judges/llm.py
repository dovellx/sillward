"""Ask tau's configured model with the API key from project config."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx
from tau_coding.paths import TauPaths
from tau_coding.provider_config import (
    ProviderConfigError,
    ProviderSettings,
    load_provider_settings,
    provider_settings_path,
    resolve_provider_selection,
)

from sillward.judges.base import JudgeFailure
from sillward.judges.remote import (
    PostingClient,
    ask,
    chat_payload,
    responses_payload,
    text_from_chat,
    text_from_responses,
)
from sillward.protocol import Request, Verdict

_SUPPORTED_APIS = frozenset({"openai-completions", "openai-responses"})


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    """One OpenAI-compatible model tau (or a test) has already resolved."""

    model: str
    base_url: str
    api: str
    api_key: str = field(repr=False)


class LlmJudge:
    """Call tau's configured model over HTTP and parse a protocol verdict."""

    name = "llm"

    def __init__(self, *, endpoint: ModelEndpoint, client: PostingClient) -> None:
        self._endpoint = endpoint
        self._client = client

    @classmethod
    def from_tau(
        cls,
        *,
        home: Path,
        api_key: str,
        client: httpx.Client,
        provider_name: str | None = None,
        model: str | None = None,
    ) -> LlmJudge:
        """Build a judge from tau's provider settings and an explicit API key.

        ``home`` is tau's home directory. This does not read ``~/.tau`` unless
        the caller passes that path. The key is the project config value.
        Environment variables and tau's credential file are not opened.
        A missing key or an unsupported provider API is reported when
        ``judge`` is called, not while selecting the adapter.
        With no ``provider_name`` and no ``model``, the endpoint is the
        default provider's default model. Otherwise the named catalog entry
        is used. An unknown provider or model is ``unavailable`` before any
        request is sent.
        """
        try:
            if provider_name is None and model is None:
                endpoint = _endpoint_from_tau(home, api_key)
            else:
                endpoint = _selected_endpoint(home, api_key, provider_name, model)
        except ProviderConfigError as exc:
            raise JudgeFailure("unavailable") from exc
        return cls(endpoint=endpoint, client=client)

    def judge(self, request: Request) -> Verdict:
        """Ask the endpoint. Timeouts, non-200 responses, and bad JSON fail closed."""
        endpoint = self._endpoint
        if endpoint.api not in _SUPPORTED_APIS:
            raise JudgeFailure("unsupported provider")
        if not endpoint.api_key:
            raise JudgeFailure("missing api key")
        root = endpoint.base_url.rstrip("/")
        if endpoint.api == "openai-responses":
            return ask(
                self._client,
                url=f"{root}/responses",
                api_key=endpoint.api_key,
                payload=responses_payload(endpoint.model, request),
                adapter=self.name,
                request=request,
                read_text=text_from_responses,
            )
        return ask(
            self._client,
            url=f"{root}/chat/completions",
            api_key=endpoint.api_key,
            payload=chat_payload(endpoint.model, request),
            adapter=self.name,
            request=request,
            read_text=text_from_chat,
        )


def _endpoint_from_tau(home: Path, api_key: str) -> ModelEndpoint:
    paths = TauPaths(home=home)
    settings = _settings(paths)
    provider = settings.get_provider()
    return ModelEndpoint(
        model=provider.default_model,
        base_url=provider.base_url,
        api=str(getattr(provider, "api", "")),
        api_key=api_key.strip(),
    )


def _selected_endpoint(
    home: Path,
    api_key: str,
    provider_name: str | None,
    model: str | None,
) -> ModelEndpoint:
    paths = TauPaths(home=home)
    selection = resolve_provider_selection(
        _settings(paths),
        provider_name=provider_name,
        model=model,
    )
    provider = selection.provider
    return ModelEndpoint(
        model=selection.model,
        base_url=provider.base_url,
        api=str(getattr(provider, "api", "")),
        api_key=api_key.strip(),
    )


def _settings(paths: TauPaths) -> ProviderSettings:
    if provider_settings_path(paths).is_file():
        return load_provider_settings(paths)
    return ProviderSettings()
