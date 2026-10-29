# Contributing

Thanks for helping. Cloakroom is a one-person project, so small, focused changes are the easiest to review.

## Found a way around it?

That's a security bug. Report it privately (see [SECURITY.md](SECURITY.md)), not in a public issue.

## Bugs and false alarms

Open an issue with:

- the Cloakroom version (`cloakroom --version`), your OS, and the mode (`full` or `rules`)
- which agent you use (Claude Code, Codex CLI or Gemini CLI)
- a small example that shows the problem, and what `cloakroom explain FILE` says about it

Use fake values only. Never paste a real key, password or someone's personal data into an issue.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
git clone https://github.com/shanjeevrajendran/cloakroom
cd cloakroom
uv sync                     # installs Cloakroom and pytest into .venv
uv run pytest -q tests      # rules mode, no model download
```

Quick benchmark in rules mode:

```bash
uv run python bench/gen_corpus.py > bench/corpus.jsonl
uv run python bench/run_bench.py cloakroom_rules
```

## Rules for a change

- **Fail closed.** If a check can't run, it stops the call. It never lets the call through unchecked.
- **Never break the agent it protects.** A prompt still goes through with a warning, and every hold says what to do next.
- **Add a test** for what you changed. Pull requests run the tests on Linux, macOS and Windows.
- **Don't edit the hook files by hand.** `plugin/hooks/hooks.json`, `plugin/scripts/hook.sh` and
  `plugin/scripts/hook.ps1` come from `scripts/gen_hooks.py`. Change the script, run it, and check with
  `python scripts/gen_hooks.py --check`.
- **Detection changes need numbers.** Say what the benchmark shows before and after. Don't tune on a holdout set
  before its first run (see [bench/RESULTS.md](bench/RESULTS.md)).
- **Add a line to [CHANGELOG.md](CHANGELOG.md)** in plain English, written for the person using Cloakroom.

## License

By contributing, you agree your work is released under the [Apache-2.0 license](LICENSE). Your first pull request asks you to sign the [CLA](CLA.md) once, with a comment; the bot does the rest.
