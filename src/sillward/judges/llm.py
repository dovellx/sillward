"""Ask the model tau already has logged in, and require protocol JSON back."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from tau_coding.credentials import CredentialStoreError, FileCredentialStore, credentials_path
from tau_coding.paths import TauPaths
from tau_coding.provider_config import (
    ProviderConfigError,
    ProviderSettings,
    load_provider_settings,
    provider_settings_path,
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
    """Call tau's logged-in model over HTTP and parse a protocol verdict."""

    name = "llm"

    def __init__(self, *, endpoint: ModelEndpoint, client: PostingClient) -> None:
        self._endpoint = endpoint
        self._client = client

    @classmethod
    def from_tau(
        cls,
        *,
        home: Path,
        environ: Mapping[str, str],
        client: httpx.Client,
    ) -> LlmJudge:
        """Build a judge from tau's provider settings and credential store.

        ``home`` is tau's home directory. This does not read ``~/.tau`` unless
        the caller passes that path. A missing key or an unsupported provider
        API is reported when ``judge`` is called, not while selecting the adapter.
        """
        try:
            endpoint = _endpoint_from_tau(home, environ)
        except (ProviderConfigError, CredentialStoreError) as exc:
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


def _endpoint_from_tau(home: Path, environ: Mapping[str, str]) -> ModelEndpoint:
    paths = TauPaths(home=home)
    settings = _settings(paths)
    provider = settings.get_provider()
    return ModelEndpoint(
        model=provider.default_model,
        base_url=provider.base_url,
        api=str(getattr(provider, "api", "")),
        api_key=_api_key(provider, environ, paths),
    )


def _settings(paths: TauPaths) -> ProviderSettings:
    if provider_settings_path(paths).is_file():
        return load_provider_settings(paths)
    return ProviderSettings()


def _api_key(provider: object, environ: Mapping[str, str], paths: TauPaths) -> str:
    env_name = getattr(provider, "api_key_env", "")
    if isinstance(env_name, str) and env_name:
        from_env = environ.get(env_name, "").strip()
        if from_env:
            return from_env
    credential_name = getattr(provider, "credential_name", None)
    if not isinstance(credential_name, str) or not credential_name:
        return ""
    stored = FileCredentialStore(credentials_path(paths)).get(credential_name)
    if stored is None:
        return ""
    return stored.strip()
