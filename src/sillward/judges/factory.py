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
    The jev host is not in the plan, so the selected jev adapter has no base
    URL until a caller supplies one.
    """
    key = environ.get("TYPESAFE_API_KEY", "").strip()
    if config.judge == "jev" and key:
        return JevJudge(
            model=config.jev_model or "",
            api_key=key,
            base_url=None,
            client=client,
        )
    return LlmJudge.from_tau(home=tau_home, environ=environ, client=client)
