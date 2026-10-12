# Setup details

macOS, Windows and Linux; Python 3.10+. The first run downloads the detection model (PII-Tracer) and keeps it
loaded in a small local service. No GPU needed. Without the extras, use rules-only mode
(`CLOAKROOM_MODE=rules`): instant, but it misses names and free-text PII.

The `cloakroom` command must be on PATH where Claude Code runs; `cloakroom doctor` warns if it isn't. Without it
the hooks deny every tool call, and prompts carry a warning. On Windows, after `uv tool install`, run
`uv tool update-shell` and open a new terminal (pipx: `pipx ensurepath`).

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
