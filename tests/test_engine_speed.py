"""The fast forms of three detection steps give what the plain forms gave. Each plain form compared every finding
with every other one (or searched the whole text once per value), which took seconds on a large CSV."""
import random

import pytest

from cloakroom import engine, rules


def _random_findings(rng, n, span=400):
    out = []
    for _ in range(n):
        a = rng.randint(0, span)
        out.append(rules.Finding(a, a + rng.randint(1, 30), rng.choice(["SECRET", "EMAIL", "PERSON", "ACCOUNT"]),
                                 rng.choice(["email", "keyed-secret", "model:secret", "repeat"]), rng.random() < 0.2))
    return out


def _ranked(findings):
    return sorted(findings, key=lambda f: (-(f.keyed and f.kind == "SECRET"), -(f.end - f.start), f.start))


@pytest.mark.parametrize("seed", range(40))
def test_merge_keeps_what_the_plain_merge_kept(seed):
    rng = random.Random(seed)
    findings = _random_findings(rng, rng.randint(0, 120), span=rng.choice([40, 400, 4000]))
    assert engine._merge(findings) == engine._merge_slow(_ranked(findings))


def test_merge_with_an_empty_finding_takes_the_plain_way():
    findings = [rules.Finding(5, 5, "SECRET", "x"), rules.Finding(3, 8, "EMAIL", "email"),
                rules.Finding(8, 12, "PERSON", "model:private_person")]
    assert engine._merge(findings) == engine._merge_slow(_ranked(findings))


def test_merge_result_is_in_text_order_and_never_overlaps():
    rng = random.Random(1)
    kept = engine._merge(_random_findings(rng, 500, span=3000))
    assert all(a.end <= b.start for a, b in zip(kept, kept[1:]))


@pytest.mark.parametrize("seed", range(25))
def test_covered_matches_the_plain_check(seed):
    rng = random.Random(seed)
    spans, at = [], 0
    for _ in range(rng.randint(0, 30)):
        at += rng.randint(0, 12)
        end = at + rng.randint(1, 25)
        spans.append((at, end))
        at = end
    starts = [a for a, _ in spans]
    for _ in range(300):
        a = rng.randint(0, at + 5)
        b = a + rng.randint(0, 20)
        assert engine._covered(spans, starts, a, b) == any(s <= a and b <= e for s, e in spans)


@pytest.mark.parametrize("seed", range(15))
def test_repeats_in_one_pass_finds_what_one_search_per_value_found(seed, monkeypatch):
    rng = random.Random(seed)
    words = ["anna", "annabel", "bel anna", "aaaa", "aaaaaa", "kai.x@quillmere.net", "x@quillmere", "4471 22", "22 4471"]
    text = " ".join(rng.choice(words + ["the", "and", "aa", "a"]) for _ in range(rng.randint(5, 400)))
    found = []
    for w in rng.sample(words, rng.randint(1, len(words))):
        i = text.find(w)
        if i >= 0:
            found.append(rules.Finding(i, i + len(w), rng.choice(["PERSON", "EMAIL"]), "x"))
    monkeypatch.setattr(engine, "_REPEAT_SCANS", 10 ** 12)
    plain = engine._repeats(text, found)
    monkeypatch.setattr(engine, "_REPEAT_SCANS", -1)
    one_pass = engine._repeats(text, found)
    key = lambda f: (f.start, f.end, f.kind)  # noqa: E731
    assert sorted(one_pass, key=key) == sorted(plain, key=key)


def test_a_large_customer_list_is_masked_whole():
    rows = [f"row {i}, user{i}@quillmere.net, 415-820-{4000 + i:04d}, note" for i in range(3000)]
    text = "\n".join(rows + rows[:50])  # the first 50 again: every repeat is found
    out = engine.mask(text, mode="rules").text
    assert "@quillmere.net" not in out and "415-820-" not in out
    assert out.count("PII_EMAIL_1_") == 2


def test_values_nested_too_deep_for_one_pattern_take_the_plain_way(monkeypatch):
    values = ["ab" * n for n in range(2, 700)]  # each one starts with the one before: no pattern holds that
    text = values[-1] + " " + values[-1]
    found = [rules.Finding(0, len(v), "SECRET", "x") for v in values]
    monkeypatch.setattr(engine, "_REPEAT_SCANS", 10 ** 12)
    plain = engine._repeats(text, found)
    monkeypatch.setattr(engine, "_REPEAT_SCANS", -1)
    key = lambda f: (f.start, f.end, f.kind)  # noqa: E731
    assert sorted(engine._repeats(text, found), key=key) == sorted(plain, key=key)
