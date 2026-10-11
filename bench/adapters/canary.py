import _ts
NAME = "sensitive-canary (blocks)"


def mask(text):
    return text, _ts.run(text)["canary"]["blocked"]
