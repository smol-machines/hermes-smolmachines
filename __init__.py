"""Hermes terminal backend that runs the agent's commands in a smol machine on this computer.

    hermes plugins install smolmachines
    hermes config set terminal.backend smolmachines
"""

from __future__ import annotations

import importlib.util
from typing import Any, Optional

from agent.terminal_env_provider import TerminalEnvironmentProvider

DEFAULT_IMAGE = "python:3.12-slim"


class SmolMachinesProvider(TerminalEnvironmentProvider):
    name = "smolmachines"
    display_name = "smol machines"

    def __init__(self, get_config: Any) -> None:
        self._get_config = get_config

    @property
    def description(self) -> str:
        return "Run terminal, file and code tools in a microVM on this computer, with its own Linux kernel."

    @property
    def strip_env_keys(self) -> frozenset:
        return frozenset({"SMOL_CLOUD_TOKEN"})

    def is_available(self) -> bool:
        return importlib.util.find_spec("smol") is not None

    def create_environment(
        self,
        *,
        cwd: str,
        timeout: int,
        task_id: str = "default",
        image: Optional[str] = None,
        container_config: Optional[dict] = None,
        **kwargs: Any,
    ):
        from .environment import SmolMachineEnvironment

        cc = container_config or {}
        return SmolMachineEnvironment(
            cwd=cwd,
            timeout=timeout,
            task_id=task_id,
            image=image or self._get_config("image", DEFAULT_IMAGE) or DEFAULT_IMAGE,
            cpus=max(1, int(cc.get("container_cpu") or 1)),
            memory_mb=int(cc.get("container_memory") or 5120),
            persistent=bool(cc.get("container_persistent", True)),
        )


def register(ctx) -> None:
    ctx.register_terminal_environment_provider(SmolMachinesProvider(ctx.get_config))
