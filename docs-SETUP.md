# Setup details

macOS, Windows and Linux; Python 3.10+. The first run downloads the detection model (PII-Tracer) and keeps it
loaded in a small local service. No GPU needed. Without the model extras, use rules-only mode
(`CLOAKROOM_MODE=rules`): instant, but it misses names and free-text PII.

The `cloakroom` command must be on PATH where Claude Code runs; `cloakroom doctor` warns if it isn't. Without it
the hooks fail closed: prompts are blocked and tool calls denied. On Windows, after `uv tool install`, run
`uv tool update-shell` and open a new terminal (pipx: `pipx ensurepath`).

## Backends

Backends (`CLOAKROOM_BACKEND=auto|torch|onnx`, `auto` by default):

- **Apple Silicon / CUDA:** PyTorch. Install `cloakroom[torch]`.
- **CPU (no GPU):** ONNX Runtime, no PyTorch at runtime, about 9x faster than PyTorch on a typical CPU. Install
  `cloakroom[model]`, then export the model once (this step needs PyTorch, about 4 GB of disk, and under a minute
  on a fast machine): `pip install "cloakroom[model,torch]" onnx && cloakroom export-onnx`. The files go under
  `~/.cloakroom/onnx/`. `CLOAKROOM_ONNX=int8` uses a smaller quantized export (slightly faster, a few more false
  alarms). `cloakroom doctor` prints the backend in use and how long one check takes.

## Uninstall

`/plugin uninstall cloakroom@cloakroom` in Claude Code, `uv tool uninstall cloakroom` (or `pipx uninstall cloakroom`), then delete `~/.cloakroom` and the `cloakroom` entry in your OS keychain (Keychain Access on macOS, Credential Manager on Windows).
