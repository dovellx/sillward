"""Choose the llm or jev adapter from user config and the process environment."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import httpx

from sillward.config import Config
from sillward.judges.jev import JevJudge
from sillward.judges.llm import LlmJudge


def select_judge(
    config: Config,
    *,
    environ: Mapping[str, str],
    tau_home: Path,
    client: httpx.Client,
) -> LlmJudge | JevJudge:
    """Return jev only when config asks for it and ``TYPESAFE_API_KEY`` is set.

    A configured jev adapter without that key falls back to the llm adapter.
    An explicit ``judge: llm`` stays on llm even if the key is present.
    The llm adapter uses ``models.judge.api_key`` when that is set, and
    otherwise ``llm_api_key``. ``models.judge`` also selects the provider and
    model. jev does not read either key.
    The selected jev adapter calls TypeSafe System One. An empty model id
    uses ``jev-latest``.
    """
    key = environ.get("TYPESAFE_API_KEY", "").strip()
    if config.judge == "jev" and key:
        return JevJudge(
            model=config.jev_model or "",
            api_key=key,
            base_url=None,
            client=client,
        )
    target = config.models.judge
    api_key = config.llm_api_key or ""
    if target is not None and target.api_key:
        api_key = target.api_key
    return LlmJudge.from_tau(
        home=tau_home,
        api_key=api_key,
        client=client,
        provider_name=None if target is None else target.provider,
        model=None if target is None else target.model,
    )
