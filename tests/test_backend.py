"""The backend end to end: real smol machines on this computer, driven through Hermes's environment contract."""

import hashlib
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from smol import ConnectOptions, Machine, SmolError, local_availability

AVAILABLE, _, REASON = local_availability()
pytestmark = pytest.mark.skipif(not AVAILABLE, reason=f"this host cannot run smol machines: {REASON}")
LOCAL = ConnectOptions(target="local")


ROOT = Path(__file__).resolve().parents[1]
# The plugin imported the way Hermes imports a directory plugin: a package rooted at the repository.
LOAD = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("hermes_smolmachines", {str(ROOT / "__init__.py")!r},
                                              submodule_search_locations=[{str(ROOT)!r}])
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)
provider = plugin.SmolMachinesProvider(lambda key, default=None: default)
"""


@pytest.fixture(scope="module")
def provider():
    scope = {}
    exec(LOAD, scope)
    return scope["provider"]


def open_env(provider, task_id, persistent):
    return provider.create_environment(cwd="/root", timeout=60, task_id=task_id,
                                       container_config={"container_persistent": persistent})


def exists(name):
    try:
        Machine.connect(name, LOCAL)
    except SmolError as exc:
        if exc.code == "NOT_FOUND":
            return False
        raise
    return True


def test_commands_run_in_a_local_machine_that_cleanup_deletes(provider, monkeypatch, tmp_path):
    # A token for the cloud must not send the work there: with it, a cloud create would fail on this fake key.
    monkeypatch.setenv("SMOL_CLOUD_TOKEN", "not-a-real-token")
    env = open_env(provider, f"run-{uuid.uuid4().hex[:6]}", persistent=False)
    name = env._name
    try:
        result = env.execute("echo out; echo err >&2; exit 3")
        assert result["returncode"] == 3 and result["output"].split() == ["out", "err"]

        env.execute("cd /tmp && export GREETING=hi")
        assert env.execute("pwd; echo $GREETING")["output"].split() == ["/tmp", "hi"]

        payload = "line one\nno trailing newline é"
        assert env.execute("cat > /tmp/in.txt", stdin_data=payload)["returncode"] == 0
        env.fetch_file("/tmp/in.txt", tmp_path / "out.txt", max_bytes=1 << 20)
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == payload

        # Past the engine's 11 MiB stream cap: the kept output comes back marked, and files still arrive whole.
        assert "[OUTPUT TRUNCATED" in env.execute("yes | head -c 12582912")["output"]
        env.execute("head -c 12582912 /dev/urandom > /tmp/big.bin")
        env.fetch_file("/tmp/big.bin", tmp_path / "big.bin", max_bytes=50 << 20)
        guest_sha = env.execute("sha256sum /tmp/big.bin")["output"].split()[0]
        assert hashlib.sha256((tmp_path / "big.bin").read_bytes()).hexdigest() == guest_sha

        assert env.execute("sleep 30", timeout=2)["returncode"] == 124
        assert env.execute("echo alive")["output"].strip() == "alive"
    finally:
        env.cleanup()
    assert not exists(name)


def test_each_session_keeps_its_own_persistent_machine(provider):
    # Gateway keys share a long prefix: only the chat id at the end tells two users apart.
    prefix = f"session:agent:main:telegram:dm:{uuid.uuid4().hex[:6]}"
    alice = open_env(provider, prefix + "111111111", persistent=True)
    bob = open_env(provider, prefix + "122222222", persistent=True)
    names = [alice._name, bob._name]
    try:
        alice.execute("echo alice > /root/secret")
        alice.cleanup()
        alice = open_env(provider, prefix + "111111111", persistent=True)
        assert alice.execute("cat /root/secret")["output"].strip() == "alice"
        assert "No such file" in bob.execute("cat /root/secret")["output"]
    finally:
        for name in names:
            Machine.connect(name, LOCAL).delete()


def test_a_shared_machine_outlives_another_process_and_reboots_when_stopped(provider):
    task_id = f"shared-{uuid.uuid4().hex[:6]}"
    other = subprocess.Popen(
        [sys.executable, "-c", LOAD + f"""
env = provider.create_environment(cwd="/root", timeout=60, task_id={task_id!r}, container_config={{}})
env.execute("echo shared > /root/note")
print("ready", flush=True)
sys.stdin.readline()
env.cleanup()
"""], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert other.stdout.readline().strip() == "ready"
    env = open_env(provider, task_id, persistent=True)
    try:
        other.communicate("exit\n", timeout=60)  # the process that booted the machine cleans up and exits
        result = env.execute("cat /root/note")
        assert result["output"].strip() == "shared" and not result.get("environment_recreated")

        Machine.connect(env._name, LOCAL).stop()
        result = env.execute("cat /root/note")
        assert result["output"].strip() == "shared" and result["environment_recreated"]
    finally:
        Machine.connect(env._name, LOCAL).delete()
