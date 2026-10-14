# Precision levers, CredData tune half (2026-10-14)

Tune half = half the CredData repositories by repo-id hash (`SPLIT=tune`), scored in full mode (rules + model),
one lever per run. Holdout leaks = values leaked on holdout v2..v7 (`bench/rerun.py`), baseline 0/15/0/1/3/0.
Only counts here, never values.

| # | Lever | tp | fp | fn | P | R | F1 | Holdout leaks v2-v7 | Kept? | Why |
|---|-------|----|----|----|---|---|----|---------------------|-------|-----|
| 0 | Baseline (0.2.4) | 2664 | 1890 | 642 | 0.585 | 0.806 | 0.678 | 0/15/0/1/3/0 | - | Matches the brief's 0.58 / 0.81 |
| 1 | Scrubbed values: a value with a scrub word as one of its parts (`*_removed`, `REDACTED_*`, `masked`, `sanitized`...) counts as already redacted | 2664 | 1342 | 642 | 0.665 | 0.806 | 0.729 | 0/15/0/1/3/0 | Kept | -548 false alarms, mostly recorded HTTP fixtures with a scrubbed Basic header; no true value had such a word |
| 2 | Template values: a value that starts like a template (`$t(key)`, `${`, `{{`, `%(`, `{0}`) is a reference | 2664 | 1181 | 642 | 0.693 | 0.806 | 0.745 | 0/15/0/1/3/0 | Kept | -161 false alarms (translation files whose password labels start with `$t(...)`); no true value starts this way |
| 3 | Variable references (keyed-secret rule only): an unquoted identifier or dotted path under a secret key that ends in the secret word and has no digits (`user.token`, `opts.AccessToken`), or a quoted UPPER_SNAKE constant (`NATS_SECRET`) | 2664 | 1111 | 642 | 0.706 | 0.806 | 0.752 | 0/15/0/1/3/0 | Kept | -70 false alarms, no recall lost; +0.007 F1, the first gain under 0.01. First version (any word segment) gave 0.707 / 0.753 but let an unquoted literal like `hunter2Password99` through; tightened before commit, the row shows the committed version |
| 4 | Word-start key names: the secret key name must start a word or a camelCase part, not sit inside a longer word (`minipass`) or a file path (`.../password-x.png: Added.`) | 2662 | 1062 | 644 | 0.715 | 0.805 | 0.757 | 0/15/0/1/3/0 | Dropped | +0.004 F1 for -43 false alarms, but it lost 2 true values under glued lower-case keys (`identitytoken`, Docker's credential field; `keypassword`). Second gain under 0.01 in a row: stop rule |

Stopped after lever 4 (two consecutive levers under +0.01 F1). Final = levers 1-3: tune P 0.706 / R 0.806 / F1 0.752.
Considered and not run: AWS ARNs as public names (29 of 134 hits are labelled real KMS key ids), lone model "person"
spans (they also catch real synthetic passwords; needs the dashboard tune set).

## Final checks (committed code, read once)
| Check | 0.2.4 | Now |
|---|---|---|
| CredData holdout half, test vectors excluded (`SPLIT=holdout EXCLUDE_REPOS=2ba83c6a`): P / R / F1 | 0.710 / 0.693 / 0.702 | 0.716 / 0.693 / 0.704 |
| Holdout leaks v2..v7 | 0/15/0/1/3/0 | 0/15/0/1/3/0 |
| Holdout false alarms v2..v7 | 2/2/4/3/12/3 | 2/2/4/3/12/3 |
| Gretel P / R | 0.518 / 0.848 | 0.518 / 0.848 |
| ai4privacy P / R | 0.911 / 0.915 | 0.911 / 0.914 |
| TAB P / R | 0.813 / 0.951 | 0.813 / 0.951 |

The holdout half gains far less precision than the tune half (-50 false alarms against -779): the scrubbed-value
and translation-template patterns cluster in a few tune-half repositories. Holdout-half recall (0.693) is unchanged
from 0.2.4 and below the 0.71 goal; that gap predates this work.
