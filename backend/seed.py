import argparse
from datetime import date, timedelta
from decimal import Decimal
from random import Random

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import local_today
from .database import get_engine
from .models import Accrual, Contract, ContractStatus, ContractType, Counterparty, Payment, Property
from .models import PropertyStatus, PropertyType
from .services.dashboard import shift_month

OBJECT_NAMES = {
    PropertyType.BUILDING: ("Административное здание", "Городская мастерская", "Здание склада", "Бывшая школа"),
    PropertyType.LAND: ("Участок под благоустройство", "Участок под застройку", "Земля у парковой зоны"),
    PropertyType.PREMISES: ("Офисное помещение", "Торговый павильон", "Помещение в центре", "Нежилой блок"),
    PropertyType.OTHER: ("Муниципальный гараж", "Инженерная площадка", "Хозяйственный блок"),
}
STREETS = ("Клёновая", "Тихая", "Лесная", "Речная", "Солнечная", "Полевая", "Садовая", "Спортивная")
TABLES = (Property, Counterparty, Contract, Accrual, Payment)


def make_properties(random: Random, today: date):
    result = []
    for i in range(100):
        kind = random.choices(list(PropertyType), weights=[30, 31, 28, 11])[0]
        month = shift_month(today, -(i // 9))
        created = min(today, month + timedelta(days=random.randrange(24)))
        result.append(Property(
            name=f"{random.choice(OBJECT_NAMES[kind])} № {i + 1}",
            type=kind,
            address=f"г. Береговой, ул. {random.choice(STREETS)}, {i + 1}",
            area=Decimal(random.randint(23, 1800)),
            cadastral_number=f"66:00:0000000:{1001 + i}" if i % 6 != 0 else None,
            status=random.choices(list(PropertyStatus), weights=[24, 67, 9])[0],
            created_at=created,
            note="Демонстрационная запись.",
        ))
    return result


def make_contracts(random: Random, today: date, properties, parties):
    contracts = []
    fixed_terms = (0, 18, 30, 31, 90, 91, 180, 181, 310, -12)
    for i in range(124):
        kind = random.choices(list(ContractType), weights=[46, 23, 18, 13])[0]
        status = (
            ContractStatus.ACTIVE if i < len(fixed_terms)
            else random.choices(list(ContractStatus), weights=[77, 18, 5])[0]
        )
        days = fixed_terms[i] if i < len(fixed_terms) else random.randint(-150, 360)
        if status == ContractStatus.COMPLETED:
            days = -random.randint(1, 125)
        end = today + timedelta(days=days)
        eligible = [item for item in properties if item.created_at <= min(today, end)]
        prop = random.choice(eligible)
        start = max(prop.created_at, today - timedelta(days=random.randint(145, 350)))
        if kind == ContractType.SALE and i >= len(fixed_terms) and i % 2 == 0:
            end = None
        contracts.append(Contract(
            number=f"ДЕМО-{i + 1:03d}", type=kind, status=status, property_id=prop.id,
            counterparty_id=random.choice(parties).id, start_date=min(start, end) if end else start,
            end_date=end, amount=Decimal(random.randint(45, 980) * 1000), note="Демонстрационный договор.",
        ))
    return contracts


def populate(db: Session, today: date) -> tuple[int, int, int, int, int] | None:
    # Coordinate simultaneous seeds and writes before checking for existing records.
    db.execute(text("LOCK TABLE properties, counterparties, contracts, accruals, payments IN SHARE ROW EXCLUSIVE MODE"))
    if any(db.scalar(select(model.id).limit(1)) is not None for model in TABLES):
        return None

    random = Random(2814)
    properties = make_properties(random, today)
    parties = [Counterparty(name=f"Демонстрационный контрагент {i:02d}") for i in range(1, 25)]
    db.add_all(properties + parties)
    db.flush()

    contracts = make_contracts(random, today, properties, parties)
    db.add_all(contracts)
    db.flush()

    accruals = []
    for i, contract in enumerate(contracts):
        latest = min(today, contract.end_date) if contract.end_date else today
        for j in range(2 + i % 2):
            span = (latest - contract.start_date).days
            accrued = contract.start_date + timedelta(days=round(span * (j + random.random()) / (2 + i % 2)))
            accruals.append(Accrual(
                contract_id=contract.id, amount=Decimal(random.randint(85, 1400)) * 100,
                accrual_date=accrued, due_date=accrued + timedelta(days=random.randint(12, 34)),
                description="Демонстрационное начисление",
            ))
    db.add_all(accruals)
    db.flush()

    payments = []
    for i, accrual in enumerate(accruals):
        if i % 5 == 0:
            continue
        delay = min((today - accrual.accrual_date).days, random.randint(6, 39))
        paid_date = accrual.accrual_date + timedelta(days=delay)
        if i % 5 == 1:
            parts = [(accrual.amount * Decimal("0.35"), paid_date)]
        elif i % 5 == 2:
            first = accrual.amount * Decimal("0.45")
            second_date = min(today, paid_date + timedelta(days=17))
            parts = [(first, paid_date), (accrual.amount - first, second_date)]
        else:
            parts = [(accrual.amount, paid_date)]
        payments.extend(Payment(accrual_id=accrual.id, amount=amount, payment_date=day) for amount, day in parts)
    db.add_all(payments)
    return len(properties), len(parties), len(contracts), len(accruals), len(payments)


def main() -> None:
    parser = argparse.ArgumentParser(description="Заполнить пустую проектную БД демонстрационными данными.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=local_today(), help="Опорная дата в формате ГГГГ-ММ-ДД.")
    today = parser.parse_args().as_of
    if today < date(2, 1, 1) or today > date(9998, 12, 31):
        parser.error("Укажите опорную дату в диапазоне от 0002 до 9998 года.")
    with Session(get_engine()) as db, db.begin():
        counts = populate(db, today)
    if counts:
        print("Добавлены объекты, контрагенты, договоры, начисления и платежи:", *counts)
    else:
        print("В проектной БД уже есть записи. Посев пропущен.")


if __name__ == "__main__":
    main()
