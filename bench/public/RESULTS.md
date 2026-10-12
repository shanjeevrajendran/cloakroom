# Cloakroom on public PII datasets (2026-10-11, run once, no tuning)

Independent of the sets we wrote ourselves (`bench/RESULTS.md`). Cloakroom v0.2 branch (c4e2c58), full mode
(rules + the PII model, PyTorch on Apple GPU), default settings.

| Dataset | Items | Recall | Recall, placeholders excluded | Precision | F1 | ms/item |
|---|---|---|---|---|---|---|
| [Gretel synthetic PII finance](https://huggingface.co/datasets/gretelai/synthetic_pii_finance_multilingual) (English test, 1,000 of 2,891, seed 7) | 1,000 | 0.850 | 0.935 | 0.423 | 0.565 | 46 |
| [TAB, ECHR court rulings](https://github.com/NorskRegnesentral/text-anonymization-benchmark) (test split, DIRECT identifiers) | 127 | 0.951 | 0.951 | 0.745 | 0.835 | 96 |

Rules alone (no model): Gretel recall 0.160, TAB 0.000. Names and addresses need the model.

### After four fixes (same samples, run once after the fixes were built on other rows / TAB dev)
| Dataset | Recall | Precision | F1 |
|---|---|---|---|
| Gretel | 0.850 (=) | 0.445 (+0.022) | 0.584 (+0.019) |
| TAB | 0.951 (=) | 0.745 (=) | 0.835 (=) |

### After the plain-date change (2026-10-11, test split run once, no tuning on it)
Dates the model finds are masked only near birth / death / age words or as part of a person's record (next to a
name, or in a row that holds one); other dates stay visible. `"mask_all_dates": true` restores the old behaviour.
| Dataset | Recall | Precision | F1 |
|---|---|---|---|
| Gretel | 0.848 (-0.002) | 0.511 (+0.066) | 0.638 (+0.054) |
| TAB | 0.951 (=) | 0.813 (+0.068) | 0.876 (+0.041) |

### After the role-words fix (2026-10-12, built on Gretel dev rows, test split run once)
"Support Agent", "Service Team", "Phone Number" and similar all-generic phrases are no longer read as names.
Gretel: recall 0.848 (=), precision 0.516 (+0.005, 45 fewer false alarms), F1 0.642. TAB unchanged (0.951 / 0.813).

Fixes: values under address keys (`"street": "..."`, `Address:`), birth dates in ISO date-time and written forms,
short passwords / PINs under their keys, and SWIFT / FIX field tags no longer read as account numbers. Built and
checked on a separate sample (other rows; TAB dev split); our own corpus + holdout v1-v6 are unchanged, leak for leak.

## How it is scored
- **Must mask** (recall): names, emails, phones, street addresses, birth dates, government IDs, card and bank numbers,
  account / customer / employee IDs, usernames, passwords, API keys. For TAB, its DIRECT identifiers. A value only
  partly masked counts as missed.
- **Neutral**: labels Cloakroom doesn't aim at (company, city, plain date, IP, SWIFT code; TAB QUASI identifiers).
  Masking them is neither right nor wrong.
- **False alarm** (precision): a mask over text no label covers, or TAB text labelled "don't mask".
- **Placeholders**: Cloakroom leaves obvious example values alone on purpose (`(123) 456-7890`, `johndoe@example.com`,
  `John Doe`). Gretel labels them as PII; the second recall column leaves those out.

## What it says
- Gretel's low precision is mostly not wrong masks: about a third are SWIFT / FIX message field tags read as account
  numbers (`:35E:`, `54=1`, `77S`) - a real fix - and much of the rest is dates and account numbers Gretel doesn't
  label. Cloakroom masks plain dates by design.
- TAB's false alarms are plain dates (46) and lone first names (41).

Reproduce: download the files listed in `run_public.py`, then
`python bench/public/run_public.py DATA --mode full`. Examples with values go to `DATA/results/`, never the repo.
