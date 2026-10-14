---
title: How I keep secrets and personal data out of Claude Code
description: Cloakroom masks secrets and personal data before Claude Code's model sees them, and puts the real values back when Claude writes.
---

# How I keep secrets and personal data out of Claude Code

## The problem

Claude Code reads whatever a task touches. Ask it to fix a failing job and it opens the `.env`, tails the log, greps the support export. Everything it reads goes to the model as context.

But my working folders hold API keys, database passwords, and support tickets with customers' names, emails and home addresses. I didn't want any of that leaving my machine just because Claude needed to look at a file that happened to contain it.

Secret scanners help with keys that have a known shape. They don't help with a customer's name in a ticket, or an address in a log line. To a regex, those look like ordinary text.

So I built Cloakroom: a Claude Code plugin that masks secrets and personal data before the model sees them, and puts the real values back when Claude writes.

## What Claude sees

Claude Code lets a plugin run hooks before and after every tool call. Cloakroom sits on those hooks. When Claude reads a file or runs a command, the output goes through Cloakroom first. It looks for:

- secrets with a known shape (cloud keys, tokens, private keys, passwords in URLs and config lines)
- numbers with a checksum (cards, IBANs, SSNs)
- personal data in free text (names, emails, addresses, birth dates), found by a small model that runs locally

Each value it finds is swapped for a placeholder. Here's a support note and a config line, on disk and as Claude gets them:

```
# on disk
Nadia Petrov called about a double charge. Reach her at nadia.petrov@gmail.com, 18 Larkspur Lane, Charlotte NC.
DATABASE_URL=postgres://admin:Hunter2x9@10.0.4.12/prod

# what Claude sees
PII_PERSON_1 called about a double charge. Reach her at PII_EMAIL_1, PII_ADDRESS_1.
DATABASE_URL=postgres://admin:PII_SECRET_1@10.0.4.12/prod
```

Claude can still do the job. It knows there's a person, an email and a password, and where each one sits. It just doesn't know the values. A value keeps the same placeholder for the whole session, so `PII_PERSON_1` in one file is the same person in the next.

## Scans and photos

A lot of personal data lives in pictures: a scanned form, a photo of an ID. Cloakroom can't check pixels, so it reads them instead. A scanned PDF page or a photo that Claude opens goes through OCR on your own machine, and Claude gets the masked text instead of the image. The OCR models ship inside the package, so nothing is uploaded. A photo of a form that says `Patient: Nadia Petrov DOB 03/14/1986` reaches Claude as `Patient: PII_PERSON_1 DOB PII_DATE_1`.

OCR misreads digits, so in that text every long run of them is masked as an account number. Handwriting still reads poorly.

## How restore works

Masking alone breaks things. If Claude writes `Hi PII_PERSON_1,` into a reply draft, that's useless to you.

So the original values go into an encrypted vault for the session, with the key in your OS's secure store (Keychain on macOS, Credential Manager on Windows, Secret Service on Linux). When Claude writes a file or runs a command that contains a placeholder, the hook swaps the real value back in, locally, just before the tool runs. The draft on your disk says `Hi Nadia Petrov,`. The model never saw the name.

Prompts are different. Claude Code lets a hook block a prompt but not rewrite it, so if you paste a key or an email straight into the chat, Cloakroom stops that prompt and tells you what it found. Put the detail in a file and ask Claude to read it instead, or start the prompt with `cr!` to send it as is.

## Fixing false alarms

A detector that masks too much gets switched off. Mine flagged `Order 4471` as an account number, which is harmless and gets in the way.

`cloakroom dashboard` opens a local page listing every masked call. Label an alert as correct, a false alarm, or something it missed. A false alarm suggests a "never mask this" rule; a miss suggests an "always mask this" one. Approve the rule and that order number reaches Claude as plain text from then on. Rules that would unmask a key, password or card number always wait for your yes, and undo puts everything back.

The detection model itself never changes. Your labels become rules in front of it, encrypted like the vault.

## What I learned

**Regex isn't enough.** Keys are easy. Names, addresses and free-text personal data need a model. I use a small one that runs on your machine. The cost is a one-time download and some false alarms, which is why the labels and rules exist.

**Restore is the hard part.** Detection gets the attention, but the real work was making write-back reliable: the same placeholder everywhere in a session, the right shape for each tool's input, and edge cases like Claude's Edit tool checking text against the file before any hook runs.

**Fail closed.** If Cloakroom can't check something (the model still loading, the service down, OCR timing out), tool calls are held back rather than waved through. Prompts fall back to the fast rules, so you're never locked out.

**Encoded output counts.** A command like `base64 .env` or `xxd` turns a secret into something no pattern matches. Cloakroom decodes those dumps and checks them too.

**Know what it isn't.** Cloakroom stops accidental exposure: the key in a file Claude happened to open, the customer list in a log. It isn't a sandbox, and a model determined to leak data piece by piece is out of scope.

## Try it

It's open source, runs locally, needs no account, and works on macOS, Windows and Linux. Inside Claude Code:

```
/plugin marketplace add shanjeevrajendran/cloakroom
/plugin install cloakroom@cloakroom
```

It protects keys and card numbers within seconds, then downloads the local model in the background for names and addresses.

I'd like to hear what it misses and what it over-masks: https://github.com/shanjeevrajendran/cloakroom

---

[Cloakroom on GitHub](https://github.com/shanjeevrajendran/cloakroom) · [README](https://github.com/shanjeevrajendran/cloakroom#readme)
