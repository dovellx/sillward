"""TypeSafe System One adapter.

The implementation plan names the model id and the ``TYPESAFE_API_KEY``
environment variable, and it does not name a host. Callers pass the base URL.
Without one, ``judge`` fails before any HTTP call. When a base URL is present
the adapter posts a non-streaming chat completion and reads protocol JSON.
"""

from __future__ import annotations

from sillward.judges.base import JudgeFailure
from sillward.judges.remote import PostingClient, ask, chat_payload, text_from_chat
from sillward.protocol import Request, Verdict


class JevJudge:
    """Call a System One chat-completions endpoint."""

    name = "jev"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None,
        client: PostingClient,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client = client

    def judge(self, request: Request) -> Verdict:
        """Ask System One, or fail closed when the endpoint was not provided."""
        if self._base_url is None or not self._base_url.strip():
            raise JudgeFailure("missing endpoint")
        if not self._api_key:
            raise JudgeFailure("missing api key")
        if not self._model.strip():
            raise JudgeFailure("missing model")
        root = self._base_url.rstrip("/")
        return ask(
            self._client,
            url=f"{root}/chat/completions",
            api_key=self._api_key,
            payload=chat_payload(self._model, request),
            adapter=self.name,
            request=request,
            read_text=text_from_chat,
        )
