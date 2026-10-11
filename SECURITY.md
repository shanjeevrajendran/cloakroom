# Security

Cloakroom's job is to keep secrets and personal data away from the model, so a way around it is a security bug.

## Reporting

Please report privately through GitHub: the **Security** tab of this repo, then **Report a vulnerability**.
Don't open a public issue for a bypass; it would teach everyone the trick before there's a fix.

Useful to include: the Cloakroom version (`uv tool list` or `pip show cloakroom`), your OS, the mode
(`full` or `rules`), and a minimal example with **fake** values that shows the problem.

## What counts

- A way for a real value to reach the model despite Cloakroom running (in a tool result, a restored command, an
  encoded dump, ...).
- Problems with the vault: values written in plain text, a weak key, other local processes reading or restoring
  values.
- A placeholder restore that can be abused, for example to send a real value somewhere the user didn't intend.

Known limits that are out of scope are listed in [docs-LIMITS.md](docs-LIMITS.md).

## Response

This is a one-person project. I aim to reply within a week and to ship a fix, with credit if you want it, before
the details are public.
