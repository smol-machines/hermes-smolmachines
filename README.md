# Hermes on smol machines

Run Hermes Agent's terminal, file and code tools in a [smol machine](https://smolmachines.com): a microVM on your own computer with its own Linux kernel, booted from an OCI image.

Hermes still runs where it runs today. When it runs a command, writes a file or executes code, that work happens inside the machine instead of on your computer. The boundary is hardware virtualization (Hypervisor.framework on macOS, KVM on Linux), not a container sharing your kernel. It needs no Docker and no account.

```
you  ->  Hermes (your computer)  ->  this plugin  ->  smol machine (a Linux microVM on the same computer)
                                                       terminal, files, code run here
```

## Requirements

- macOS on Apple Silicon, or Linux on x86_64 or arm64 with glibc 2.34 or newer and access to `/dev/kvm`.
- Hermes 0.21.6 or newer.

## Install

```bash
hermes plugins install smolmachines
hermes config set terminal.backend smolmachines
```

Hermes asks to install one Python package, [`smolmachines`](https://pypi.org/project/smolmachines/), which carries the VM engine. Say yes, and yes to enabling the plugin.

Start Hermes as usual. The first command boots a machine (the first one also pulls the image). To check, ask Hermes to `run uname -a and cat /etc/os-release`: you see a Linux kernel and Debian, not your own computer.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `plugins.entries.smolmachines.settings.image` | `python:3.12-slim` | The OCI image every machine boots from. Hermes runs its tools with `bash` and `python3`, so the image needs both. |
| `terminal.container_cpu` | `1` | vCPUs per machine. |
| `terminal.container_memory` | `5120` | Memory per machine, in MB. |
| `terminal.container_persistent` | `true` | Keep one running machine per session between Hermes runs (below). |

```bash
hermes config set plugins.entries.smolmachines.settings.image ubuntu:24.04
```

How the plugin picks a machine:

- `container_persistent: true` (the Hermes default): one machine per Hermes home and session, named `hermes-<session>-<hash>`. Two gateway users, or two profiles, never share one. It keeps running when Hermes is done with it or exits, so a later session or another Hermes process on the same session finds it with your files and processes. If it was stopped, the next command boots it again from its disk and Hermes is told that background processes are gone. A machine found again keeps the image, CPUs and memory it was created with. With the `smolvm` CLI, `smolvm machine ls` lists them, and `smolvm machine stop -n <name>` or `smolvm machine delete -n <name>` frees one.
- `container_persistent: false`: every session gets its own machine, named `hermes-<session>-<random>`, deleted when the session ends and when Hermes exits.

Because the machine is a disposable sandbox with no access to your files, Hermes skips its dangerous-command prompts there, as it does for its Docker and cloud backends.

## What runs where

- Everything runs on this computer. The plugin always asks the SDK for a local machine, so nothing goes to smol machines' cloud, even when `SMOL_CLOUD_TOKEN` is set or the `smol` CLI is signed in. Hermes strips `SMOL_CLOUD_TOKEN` from the commands it runs.
- Creating a machine pulls its image from its registry (Docker Hub for `python:3.12-slim`). The engine pulls with this computer's Docker registry login when one is configured (`~/.docker/config.json`, including a `credsStore` or credential helper such as the macOS keychain). It only reads that login, and sends it only to the registry being pulled from. The SDK has no option to pull without it.
- The machine has outbound network access, so commands can install packages. It sees none of your files: nothing on the host is mounted into it.
- Machines and their disks live in smol machines' local data directory.

## Limits

- The engine stops a command once it has printed 11 MiB. Hermes gets the output up to that point, marked `OUTPUT TRUNCATED`. Send large output to a file in the machine instead.
- Files Hermes delivers from the machine (gateway attachments) are read with the SDK's file read, so they reach Hermes's own 50 MB limit.

## Not included

- Sub-agents share their parent's machine. Hermes routes a `delegate_task` child to the parent's environment before a backend is asked for one, so a backend has no place to start a child from a copy of the parent's running machine. smol machines can branch a running machine, but using that for sub-agents needs a seam in Hermes first.
- Bot Desktop, `computer_use` and the browser do not follow the terminal into the machine (`supports_streams` is not implemented).
- Windows and Intel Macs: the engine has no build for them.

## Tests

The tests boot real machines, so they run on a host that can (they skip elsewhere). With Hermes and this plugin's dependency installed in one environment:

```bash
python -m pytest -q tests/
```

## License

MIT
