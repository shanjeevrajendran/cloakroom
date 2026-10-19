# Setup details

macOS, Windows and Linux; Python 3.10+. The first run downloads the detection model (PII-Tracer) and keeps it
loaded in a small local service. No GPU needed. Without the extras, use rules-only mode
(`CLOAKROOM_MODE=rules`): instant, but it misses names and free-text PII.

The `cloakroom` command must be on PATH where Claude Code runs; `cloakroom doctor` warns if it isn't. Without it
the hooks deny every tool call, and prompts carry a warning. After a manual `uv tool install`, run
`uv tool update-shell` and open a new terminal (pipx: `pipx ensurepath`). The plugin's installer does this for you
on every system: it fetches uv when neither uv nor pipx is there (the pinned uv 0.13.1 installer from astral.sh, run
only when its SHA-256 matches), then puts `~/.local/bin` on the PATH of new terminals (`uv tool update-shell`,
`pipx ensurepath`, or for its own virtualenv the same profile line uv writes: `~/.zshenv` for zsh, `~/.bashrc` plus
`~/.profile` or `~/.bash_profile` for bash). On Windows it adds `%USERPROFILE%\.local\bin` to your user PATH.

When the install needs you, `~/.cloakroom/bootstrap.log` and the plugin's message give numbered steps for the
cause: no `curl` or `wget` (Linux: `sudo apt install curl` or `sudo dnf install curl`), no internet (allow
astral.sh, releases.astral.sh, pypi.org, files.pythonhosted.org and huggingface.co), a uv installer that doesn't
match its checksum (install uv yourself), or a failed model download (the exact command to retry). Each ends with
restarting Claude Code.

Platform limits: Intel Macs get rules-only protection (no PyTorch or ONNX Runtime builds for them; the installer
says so and sets `"mode": "rules"`). Linux aarch64 gets full protection. Linux without a Secret Service keyring
keeps the vault key in a file readable only by you (`cloakroom doctor`: "key file"); set `CLOAKROOM_VAULT_KEY`
to supply your own.

## Backends

Backends (`CLOAKROOM_BACKEND=auto|torch|onnx`, `auto` by default):

- **Apple Silicon / CUDA:** PyTorch on the GPU. Install `cloakroom[model,torch]`.
- **CPU (no GPU):** ONNX Runtime, about 9x faster than PyTorch on a typical CPU. Install `cloakroom[model,torch]`,
  then export the model once with `cloakroom export-onnx` (a few minutes; the files go under `~/.cloakroom/onnx/`).
  Until then the CPU runs PyTorch, which works but is slower. `CLOAKROOM_ONNX=int8` uses a smaller quantized export (slightly faster, a few more false
  alarms). `cloakroom doctor` prints the backend in use and how long one check takes.

## Uninstall

1. In Claude Code: `/plugin uninstall cloakroom@cloakroom`.
2. Stop the service: `cloakroom status` shows if it runs; it stops when you log out, or end the `python` process
   listening on port 37787.
3. Remove the program, with whatever installed it: `uv tool uninstall cloakroom` or `pipx uninstall cloakroom`.
   If the plugin installed it without uv or pipx, it lives in `~/.cloakroom/venv` and step 4 removes it.
4. Delete `~/.cloakroom` (config, encrypted vault, labels, rules, logs).
5. Delete the vault key: Keychain Access on macOS (search "cloakroom"), Credential Manager on Windows, or your
   Secret Service keyring on Linux.
6. Delete the downloaded model (about 1 GB): run `hf cache delete` (or `huggingface-cli delete-cache`) and pick
   `perplexity-ai/PII-Tracer`. Deleting only the `models--perplexity-ai--PII-Tracer` folder can leave the weights
   behind, since they may be stored as links to a shared folder.
