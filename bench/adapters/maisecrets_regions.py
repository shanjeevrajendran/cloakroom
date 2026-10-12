"""Mcpgate-de/maisecrets detect.scan with "regions": ["us", "uk", "in"] (the maintainers' suggested setting for
sets holding US, UK and Indian IDs; the default loads only the system locale's country)."""
import maisecrets_ as base
from maisecrets import detect, regions

NAME = "maisecrets-regions"
detect.active_regions = lambda: regions.resolve({"regions": ["us", "uk", "in"]})
detect._RULES = None


def mask(text):
    return base.mask(text)
