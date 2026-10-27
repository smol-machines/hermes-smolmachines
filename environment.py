"""The Hermes terminal environment: every command runs as `bash -c` inside one local smol machine."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import threading
import uuid
from pathlib import Path
from typing import Optional

from hermes_constants import get_hermes_home
from smol import ConnectOptions, Machine, MachineConfig, ResourceSpec, SmolError
from tools.environments.base import BaseEnvironment, FileFetchError

# An explicit local target never reaches smol machines' cloud, even when SMOL_CLOUD_TOKEN is set.
LOCAL = ConnectOptions(target="local")


class _StreamProcess:
    """A process handle over `Machine.exec_stream`: Hermes drains `stdout` and polls `poll()`."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._done = threading.Event()
        self.returncode: Optional[int] = None
        read_fd, write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
        threading.Thread(target=self._pump, args=(os.fdopen(write_fd, "wb"),), daemon=True).start()

    def _pump(self, out) -> None:
        code = 1  # a stream that ends without an exit event was killed or lost its machine
        try:
            for event in self._stream:
                kind = event["kind"]
                if kind == "exit":
                    code = event["exit_code"]
                    continue
                text = event.get("data", "")
                if kind == "error":  # the engine stops a command whose output passes its 11 MiB stream cap
                    note = event["message"]
                    text = f"\n\n... [OUTPUT TRUNCATED - {note}] ...\n\n" if "truncated" in note else f"[smol machines: {note}]\n"
                out.write(text.encode("utf-8", "replace"))
                out.flush()
        except Exception as exc:  # the reason reaches the command's output instead of a thread traceback
            try:
                out.write(f"[smol machines: {exc}]\n".encode("utf-8", "replace"))
            except OSError:
                pass  # Hermes already closed its end after a kill
        finally:
            self.returncode = code
            try:
                out.close()
            except OSError:
                pass
            self._done.set()

    def poll(self) -> Optional[int]:
        return self.returncode if self._done.is_set() else None

    def kill(self) -> None:
        self._stream.kill()

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        self._done.wait(timeout)
        return self.returncode


class SmolMachineEnvironment(BaseEnvironment):
    """Persistent: one detached machine per Hermes home and task, left running at cleanup. Otherwise: deleted."""

    def __init__(
        self, *, cwd: str, timeout: int, task_id: str, image: str, cpus: int, memory_mb: int, persistent: bool
    ) -> None:
        self._persistent, self._machine = persistent, None
        # The slug is for people. A hash of the Hermes home and the full task key keeps sessions apart.
        slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")[:24].strip("-") or "default"
        key = f"{get_hermes_home()}\0{task_id}" if persistent else uuid.uuid4().hex
        self._name = f"hermes-{slug}-{hashlib.sha256(key.encode()).hexdigest()[:12]}"
        if persistent:
            try:
                self._machine = Machine.connect(self._name, LOCAL)
            except SmolError as exc:
                if exc.code != "NOT_FOUND":
                    raise
        if self._machine is None:
            resources = ResourceSpec(cpus=cpus, memory_mb=memory_mb, network=True)
            # Detached, so a shared machine outlives whichever Hermes process booted it.
            config = MachineConfig(name=self._name, image=image, resources=resources,
                                   persistent=persistent, detach=persistent)
            self._machine = Machine.create(config, LOCAL)
        super().__init__(cwd=cwd, timeout=timeout)
        self.init_session()

    def _before_execute(self) -> None:
        if self._machine.state() == "running":
            return
        # Stopped by hand or by the host: boot it again by name, with its disk.
        self._machine = Machine.connect(self._name, LOCAL)
        self.init_session()
        self._mark_recreated()

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: Optional[str] = None):
        if stdin_data:  # exec_stream does not forward ExecOptions.stdin: stage it as a file in the machine
            staged = self._staged_stdin_path()
            self._machine.write_file(staged, stdin_data.encode("utf-8", "surrogateescape"), 0o600)
            cmd_string = self._redirect_stdin_from_file(cmd_string, staged)
        argv = ["bash", "-l", "-c", cmd_string] if login else ["bash", "-c", cmd_string]
        return _StreamProcess(self._machine.exec_stream(argv))

    def fetch_file(self, remote_path: str, local_dest: Path, *, max_bytes: int) -> None:
        """The SDK's file read: the base class's base64 over exec stops at the 11 MiB stream cap."""
        quoted = shlex.quote(remote_path)
        result = self.execute(f"[ -f {quoted} ] && stat -L -c %s {quoted}", rewrite_compound_background=False)
        size = (result.get("output") or "").split()
        if result.get("returncode") != 0 or not size or not size[-1].isdigit():
            raise FileFetchError(f"could not read {remote_path!r} in the sandbox (missing, not a regular file, or unreadable)")
        too_big = FileFetchError(f"{remote_path!r} exceeds the {max_bytes // (1024 * 1024)} MB delivery limit")
        if int(size[-1]) > max_bytes:
            raise too_big
        data = self._machine.read_file(remote_path)
        if len(data) > max_bytes:  # it grew after the size check
            raise too_big
        Path(local_dest).write_bytes(data)

    def cleanup(self) -> None:
        machine, self._machine = self._machine, None
        if machine is not None and not self._persistent:
            machine.delete()  # a persistent machine keeps running for the next session, as Hermes's contract asks
