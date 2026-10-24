# False-alarm levers, CredData tune half (0.6.2, 2026-10-23)

Same method as `bench/precision-2026-10/levers.md`: tune half = half the CredData repositories by repo-id hash
(`SPLIT=tune`), full mode (rules + model), one lever per run, each row on top of the levers kept before it. Holdout
leaks and false alarms = holdouts v2..v7 (`bench/rerun.py`); v2..v6 hold the 200 safe samples, v7 15 more. Model spans
are cached per line for the lever runs (the levers only filter after the model); the final check is uncached. Only
counts here, never values.

| # | Lever | tp | fp | fn | P | R | F1 | Holdout leaks v2-v7 | Holdout false alarms v2-v7 | Kept? | Why |
|---|-------|----|----|----|---|---|----|---------------------|----------------------------|-------|-----|
| 0 | Baseline (0.6.1, 7704e9f; same as 0.6.0) | 2664 | 1111 | 642 | 0.706 | 0.806 | 0.752 | 0/15/0/1/3/0 | 2/2/4/3/9/2 | - | |
| 1 | Versions and code constants: a value that is a package version (`0.76.8`) or range (`^1.0.5`), a number of up to 6 digits given to an UPPER_SNAKE constant whose name has no secret word anywhere in it (KEY, TOKEN, SECRET, PASS, PWD, AUTH, PIN...), or an 8/16-hex GPG key id under a key-id name | 2664 | 1065 | 642 | 0.714 | 0.806 | 0.757 | 0/15/0/1/3/0 | 2/2/4/3/9/2 | Kept | -46 false alarms (lockfile lines, syscall numbers, GPG ids), no recall lost. A first version matched the secret words as whole name parts (-56) but would let `DB_APIKEY = 482193` through; the substring check keeps those masked and gives up constants like `SYS_PKEY_FREE` |
| 2 | UI strings: under a key that is a quoted label (`'Reset Password' =>`, a space, a final period or a capital first letter) or a message key around the secret word (`passwordRequired`, `tokenAuthFailedTitle`, `incorrectPassword`), a value of two or more words with no digits is UI text (secret and person findings only) | 2664 | 985 | 642 | 0.730 | 0.806 | 0.766 | 0/15/0/1/3/0 | 2/2/4/3/9/2 | Kept | -80 false alarms, translation files in many languages. Both conditions are required, so `password: "correct horse battery staple"` and `ADMIN_PASSWORD=Summer Rain 2026` stay masked. Not tied to the file type: the benchmark (and a tool output) gives each line without its file name |
| 3 | Variable references under a password key: the lever-3 check from October (`user.password`, `req.body.password`) now also on the keyed-password rule, unquoted values only | 2664 | 941 | 642 | 0.739 | 0.806 | 0.771 | 0/15/0/1/3/0 | 2/2/4/3/9/2 | Kept | -44 false alarms, no recall lost; a literal with digits (`hunter2Password99`) or in quotes is still masked |
| 4 | AWS documentation ids: AWS's sample account ids (`111122223333`, `123456789012`) and sample key ids as whole values, and an ARN whose resource id is such a sample (or a plain name under the sample account) | 2664 | 896 | 642 | 0.748 | 0.806 | 0.776 | 0/15/0/1/3/0 | 2/2/4/3/9/2 | Kept | -45 false alarms. A first version matched the ids anywhere in a value (-85) but lost 2 real KMS key ids inside ARNs with the sample account; judging the resource id keeps them masked |
| 5 | XAML resource names: a person finding right after `x:Key="`, `x:Name="` or `x:Uid="` (`<Color x:Key="CoolBlue">`) | 2664 | 860 | 642 | 0.756 | 0.806 | 0.780 | 0/15/0/1/3/0 | 2/2/4/3/9/2 | Kept | -36 false alarms, all model "person" spans on colour and style names |
| 6 | ChangeLog file entries: the keyed rules skip a line shaped `* path/to/password-x.png: Added.` (Added, Removed, Copied, Ditto...), where the "key" is part of a file name | 2664 | 822 | 642 | 0.764 | 0.806 | 0.784 | 0/15/0/1/3/0 | 2/2/4/3/9/2 | Kept | -38 false alarms (WebKit-style ChangeLogs: `compass`, `pass.html`) |

Stopped after lever 6 (the brief's limit). Kept: 1-6. Tune half P 0.706 -> 0.764 (+0.058), recall unchanged.
Considered and not run: password hashes (bcrypt, argon2, pbkdf2) stay masked, since a hash can be cracked;
`secretRef` / `secretName` description strings in Kubernetes schemas (would need "Ref" / "Name" as message words).

## Final checks (committed code, model not cached, read once, 2026-10-24)
| Check | 0.6.1 | Now |
|---|---|---|
| CredData tune half: tp / fp / fn, P / R / F1 | 2664 / 1111 / 642, 0.706 / 0.806 / 0.752 | 2664 / 822 / 642, 0.764 / 0.806 / 0.784 |
| CredData holdout half, test vectors excluded (`SPLIT=holdout EXCLUDE_REPOS=2ba83c6a`): P / R / F1 | 0.716 / 0.693 / 0.704 (last read at 0.3.0) | 0.733 / 0.693 / 0.713 (4713 / 1714 / 2087) |
| All CredData (29,893 lines): caught / missed / false alarms, P / R / F1 | 9,353 / 5,118 / 3,903, 0.71 / 0.65 / 0.68 | 9,353 / 5,118 / 3,457, 0.730 / 0.646 / 0.686 |
| CredData without 2ba83c6a (23,667 lines) | 7,377 / 2,729 / 2,982, 0.71 / 0.73 / 0.72 | 7,377 / 2,729 / 2,536, 0.744 / 0.730 / 0.737 |
| Holdout leaks v2..v7 | 0/15/0/1/3/0 | 0/15/0/1/3/0 |
| Holdout false alarms v2..v7 (v2-v6 = the 200 safe samples) | 2/2/4/3/9/2 | 2/2/4/3/9/2 |
| Gretel P / R | 0.520 / 0.846 | 0.520 / 0.846 |
| ai4privacy P / R | 0.911 / 0.914 | 0.911 / 0.914 |
| TAB P / R | 0.813 / 0.951 | 0.813 / 0.951 |

No real secret was lost anywhere (caught counts identical on every CredData slice). All 446 removed false alarms on
the whole set are outside the test-vector repository. The holdout half gains less than the tune half (-289 false
alarms on the tune half alone): translation files and AWS documentation cluster in a few repositories. The
safe-sample false alarms (20 of 200; README said 23, v7 2 of 15, README said 3) are public landmarks and figures the
model reads as addresses and people; none of these levers touch them.
