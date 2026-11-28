from fastapi import HTTPException

FIELD_MESSAGES = {
    "name": "Укажите наименование.",
    "address": "Укажите адрес объекта.",
    "number": "Укажите номер договора.",
    "tax_id": "Укажите ИНН контрагента.",
    "type": "Выберите значение из списка.",
    "property_id": "Выберите объект.",
    "counterparty_id": "Выберите контрагента.",
    "contract_id": "Выберите договор.",
    "accrual_id": "Выберите начисление.",
    "start_date": "Укажите дату начала договора.",
    "end_date": "Укажите дату окончания договора.",
    "accrual_date": "Укажите дату начисления.",
    "due_date": "Укажите срок оплаты.",
    "payment_date": "Укажите дату платежа.",
}


class FieldError(HTTPException):
    def __init__(self, status_code, detail, field):
        super().__init__(status_code, detail)
        self.errors = [{"loc": ["body", field], "type": "value_error", "msg": detail}]


def validation_errors(errors):
    result = []
    for error in errors:
        location, kind = error["loc"], error["type"]
        field = location[-1] if location else None
        context = error.get("ctx", {})
        if kind == "value_error":
            message = str(context["error"])
        elif kind in {"missing", "string_too_short"}:
            message = FIELD_MESSAGES.get(field, "Заполните обязательное поле.")
        elif kind == "string_too_long":
            message = f"Допустимо не более {context['max_length']} символов."
        elif kind.startswith("date_"):
            message = "Укажите корректную календарную дату."
        elif kind in {"decimal_max_digits", "decimal_whole_digits"}:
            message = "Допустимо не более 12 цифр до десятичного разделителя."
        elif kind == "decimal_max_places":
            message = "Укажите не более двух знаков после десятичного разделителя."
        elif kind in {"finite_number", "decimal_parsing", "decimal_type"}:
            message = "Введите корректное число."
        elif kind == "greater_than" and field in {"amount", "area"}:
            message = "Введите положительное число."
        elif kind == "enum":
            message = "Выберите значение из списка."
        else:
            message = "Проверьте введённое значение." if field else "Проверьте данные запроса."
        result.append({"loc": location, "type": kind, "msg": message})
    return result
