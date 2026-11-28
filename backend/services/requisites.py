import re

CADASTRAL_PATTERN = r"^[0-9]+:[0-9]+:[0-9]+:[0-9]+$"


def normalize_cadastral(value):
    return (value.strip() or None) if isinstance(value, str) else value


def valid_cadastral(value):
    return len(value) <= 80 and bool(re.fullmatch(CADASTRAL_PATTERN, value))
