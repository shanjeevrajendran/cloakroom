# Setup details

macOS, Windows and Linux; Python 3.10+. The first run downloads the detection model (PII-Tracer) and keeps it
loaded in a small local service. No GPU needed. Without the extras, use rules-only mode
(`CLOAKROOM_MODE=rules`): instant, but it misses names and free-text PII.

The `cloakroom` command must be on PATH where Claude Code runs; `cloakroom doctor` warns if it isn't. Without it
the hooks fail closed: prompts are blocked and tool calls denied. On Windows, after `uv tool install`, run
`uv tool update-shell` and open a new terminal (pipx: `pipx ensurepath`).

## Backends

Backends (`CLOAKROOM_BACKEND=auto|torch|onnx`, `auto` by default):

- **Apple Silicon / CUDA:** PyTorch on the GPU. Install `cloakroom[model,torch]`.
- **CPU (no GPU):** ONNX Runtime, about 9x faster than PyTorch on a typical CPU. Install `cloakroom[model,torch]`,
  then export the model once with `cloakroom export-onnx` (a few minutes; the files go under `~/.cloakroom/onnx/`).
  Until then the CPU runs PyTorch, which works but is slower. `CLOAKROOM_ONNX=int8` uses a smaller quantized export (slightly faster, a few more false
  alarms). `cloakroom doctor` prints the backend in use and how long one check takes.

## Uninstall

`/plugin uninstall cloakroom@cloakroom` in Claude Code, `uv tool uninstall cloakroom` (or `pipx uninstall cloakroom`), then delete `~/.cloakroom` and the `cloakroom` entry in your OS keychain (Keychain Access on macOS, Credential Manager on Windows).
