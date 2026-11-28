import re


def normalize_tax_id(value):
    return re.sub(r"[\s-]", "", value) if value is not None else None


def valid_tax_id(value):
    return bool(value and re.fullmatch(r"[0-9]{10}|[0-9]{12}", value))


def clean_email(value):
    if value is None or not value.strip():
        return None
    value = value.strip()
    local, _, domain = value.rpartition("@")
    atom = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    message = "Укажите корректный адрес электронной почты."
    try:
        labels = domain.encode("idna").decode("ascii").split(".")
    except UnicodeError:
        raise ValueError(message) from None
    if (
        len(value) > 250 or len(local) > 64 or not re.fullmatch(rf"{atom}(?:\.{atom})*", local)
        or len(labels) < 2 or labels[-1].isdigit()
        or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels)
    ):
        raise ValueError(message)
    return value
