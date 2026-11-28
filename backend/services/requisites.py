import re

# The nationwide quarter uses the exact 0:0:0 prefix. Regional zero quarters
# may use a single zero or the padded format of their district.
CADASTRAL_PATTERN = r"^(0:0:0|(0[1-9]|[1-9][0-9]):([0-9]{2}:([0-9]{6,7}|0)|0:(0|0{6,7}))):[1-9][0-9]*$"
CADASTRAL_CHECK_SQL = f"cadastral_number ~ '{CADASTRAL_PATTERN}'"
CADASTRAL_KEY_PATTERN = r"(^|:)0+(?=[0-9])"
CADASTRAL_KEY_SQL = rf"regexp_replace(cadastral_number, '{CADASTRAL_KEY_PATTERN}', E'\\1', 'g')"
CADASTRAL_KEY_INDEX = "unique_property_cadastral_key_v1"


def normalize_cadastral(value):
    return (value.strip() or None) if isinstance(value, str) else value


def valid_cadastral(value):
    return len(value) <= 80 and bool(re.fullmatch(CADASTRAL_PATTERN, value))


def cadastral_key(value):
    return re.sub(CADASTRAL_KEY_PATTERN, r"\1", value)
