import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from random import Random

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import local_today
from .database import get_engine
from .models import Accrual, Contract, ContractStatus, ContractType, Counterparty, Payment, Property
from .models import PropertyStatus, PropertyType
from .seed_catalog import ARCHIVE_NOTES, PARTIES, PROPERTY_NOTES, PROPERTY_PROFILES, STREETS
from .services.counterparty import complete_tax_id, tax_check_digit
from .services.dashboard import shift_month
from .services.registry import compatible, property_status

TABLES = (Property, Counterparty, Contract, Accrual, Payment)
MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
PREFIXES = dict(zip(ContractType, ("АР", "ЗУ", "КП", "ИМ")))
RATES = {
    PropertyType.BUILDING: (165, 430),
    PropertyType.PREMISES: (330, 810),
    PropertyType.LAND: (2, 11),
    PropertyType.OTHER: (95, 320),
}
SALE_RATES = {
    PropertyType.BUILDING: (7500, 16800),
    PropertyType.PREMISES: (22100, 51500),
    PropertyType.LAND: (190, 1200),
    PropertyType.OTHER: (3100, 7700),
}


def amount(value):
    return Decimal(value).quantize(Decimal(".01"))


def lock_dataset(db):
    db.execute(text("LOCK TABLE properties, counterparties, contracts, accruals, payments IN SHARE ROW EXCLUSIVE MODE"))


def make_properties(random: Random, today: date):
    properties = []
    ranges = [(100, 1800), (450, 22000), (27, 370), (15, 210)]
    for kind, count, area_range in zip(PropertyType, (30, 28, 31, 11), ranges):
        profiles = list(PROPERTY_PROFILES[kind])
        for index in range(count):
            archived = index == 2 or index in (13, 24)
            if index < 3:
                offset = 24 + index
            elif index < 15:
                offset = index - 3
            else:
                offset = random.choices(range(23), weights=[5, 6, 4, 5, 8, 6, 4, 4, 3, 6, 4, 3] + [2] * 11)[0]
            month = shift_month(today, -offset)
            created = min(today, month + timedelta(days=random.randint(1, 24)))
            serial = len(properties) + 1
            number = random.randint(3, 160)
            part = "уч. " if kind == PropertyType.LAND else "д. "
            address = f"г. Лиственногорск, ул. {random.choice(STREETS)}, {part}{number}"
            if kind == PropertyType.PREMISES:
                address += f", пом. {random.randint(1, 16)}"
            note = random.choice(ARCHIVE_NOTES if archived else PROPERTY_NOTES) if random.random() < .43 else None
            cadastral = f"66:00:{9200100 + serial // 9:07d}:{random.randint(40, 64) * 100 + serial}"
            properties.append(Property(
                name=profiles[index % len(profiles)],
                type=kind,
                address=address,
                area=amount(Decimal(random.randint(*area_range)) + Decimal(random.randrange(0, 91, 10)) / 100),
                cadastral_number=None if serial % 11 == 0 else cadastral,
                status=PropertyStatus.ARCHIVED if archived else PropertyStatus.AVAILABLE,
                created_at=created,
                note=note,
            ))
    return properties


def tax_number(index, individual):
    width = 10 if individual else 9
    return complete_tax_id(f"{index + 7450110:0{width}d}")


def legacy_individual_tax_number(index):
    # Reproduce the old 13-digit value to identify records made by that generator.
    prefix = f"{index + 7450110:011d}"
    return (prefix + tax_check_digit(prefix, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8))
            + tax_check_digit(prefix, (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)))


def make_parties():
    return [
        Counterparty(
            name=name,
            tax_id=tax_number(index, name.startswith("ИП ")),
            email=f"{'contact' if name.startswith('ИП ') else 'office'}@{domain}.invalid",
        )
        for index, (name, domain) in enumerate(PARTIES, 1)
    ]


@dataclass
class Billing:
    contract: Contract
    cadence: int
    installment: Decimal
    cutoff: date
    arrears: bool = False


def contract_note(random, kind, status, start, end, today):
    if random.random() >= .39:
        return None
    if status == ContractStatus.TERMINATED:
        return random.choice(("Договор расторгнут по соглашению сторон.", "Сверка расчётов назначена после расторжения."))
    if status == ContractStatus.COMPLETED:
        notes = ("Акт исполнения подписан сторонами.", "Документы переданы в архив.")
        if kind in (ContractType.REAL_ESTATE_RENT, ContractType.LAND_RENT):
            notes += ("Имущество возвращено по акту.",)
        return random.choice(notes)
    if start > today:
        return random.choice(("Передача имущества запланирована к началу срока.", "Дата осмотра согласована с контрагентом."))
    if end is not None and end < today:
        return random.choice(("Срок истёк, документы переданы на сверку.", "Согласовывается дата возврата имущества."))
    notes = {
        ContractType.REAL_ESTATE_RENT: ("Плата вносится по графику договора.", "Коммунальные услуги оплачиваются отдельно.",
                                       "Предусмотрена индексация арендной платы."),
        ContractType.LAND_RENT: ("Границы передаваемой территории закреплены в приложении.",
                                "Содержание подъезда выполняет арендатор."),
        ContractType.SALE: ("Согласована оплата по графику.", "Передача имущества оформляется актом приёма-передачи."),
        ContractType.OTHER: ("Приложена схема размещения оборудования.", "Порядок доступа согласован сторонами."),
    }
    return random.choice(notes[kind])


def make_contracts(random: Random, today: date, properties, parties):
    plans = []
    sequence = defaultdict(lambda: random.randint(3, 25))

    def add(prop, party, kind, status, start, end, *, cutoff=None, previous=None, arrears=False):
        key = (kind, start.year)
        number = f"{PREFIXES[kind]}-{start.year}/{sequence[key]:03d}"
        sequence[key] += random.randint(1, 4)
        unit_area = prop.area
        planned_days = (end - start).days + 1 if end else 240
        if kind == ContractType.SALE:
            total = amount(Decimal(round(unit_area * random.randint(*SALE_RATES[prop.type]) / 1000)) * 1000)
            cadence, installment = 0, total
        else:
            low, high = RATES[prop.type]
            if kind == ContractType.OTHER:
                low, high = max(2, low // 4), max(4, high // 3)
            monthly = amount(Decimal(round(unit_area * random.randint(low, high) / 50)) * 50 + 150)
            if kind == ContractType.LAND_RENT:
                cadence = random.choices((2, 3, 6), weights=(3, 6, 2))[0]
            else:
                cadence = random.choices((1, 2, 3), weights=(2, 3, 8))[0]
            installment = monthly * cadence
            total = amount(max(monthly, monthly * Decimal(planned_days) / 30))
        note = (
            f"На основе договора № {previous.number}." if previous
            else contract_note(random, kind, status, start, end, today)
        )
        if arrears and status == ContractStatus.COMPLETED:
            note = "Окончательный расчёт по договору не произведён."
        contract = Contract(
            number=number, type=kind, status=status, property_id=prop.id, counterparty_id=party.id,
            start_date=start, end_date=end, amount=total, note=note,
        )
        plans.append(Billing(contract, cadence, installment, min(today, cutoff or end or today), arrears))
        return contract

    building, land, premises, other = ([item for item in properties if item.type == kind] for kind in PropertyType)
    active, complete, terminated = ContractStatus
    estate, land_rent, sale, miscellaneous = ContractType
    relative = lambda days: today + timedelta(days=days)

    for i, (prop, kind) in enumerate(((land[0], land_rent), (premises[0], estate), (land[1], land_rent), (building[1], estate))):
        previous_end = relative(-125 - i * 31)
        old = add(prop, parties[i], kind, complete, previous_end - timedelta(days=290), previous_end, arrears=i == 0)
        add(prop, parties[i], kind, active, previous_end + timedelta(days=10 + i * 7),
            relative((30, 80, 140, 260)[i]), previous=old, arrears=i == 1)

    add(building[0], parties[5], estate, active, relative(-304), relative(18))
    add(building[0], parties[8], estate, active, relative(-115), relative(228))
    add(premises[1], parties[7], estate, active, relative(20), relative(360))
    add(land[0], parties[9], land_rent, active, relative(-380), relative(-20))
    add(building[2], parties[2], estate, complete, relative(-540), relative(-278))
    add(other[0], parties[10], miscellaneous, terminated, relative(-295), relative(74), cutoff=relative(-63), arrears=True)
    add(premises[1], parties[11], sale, active, relative(-210), None, arrears=True)
    add(other[1], parties[12], miscellaneous, complete, relative(-217), relative(47), cutoff=relative(-38), arrears=True)

    while len(plans) < 136:
        kind = random.choices(list(ContractType), weights=(49, 27, 12, 12))[0]
        state = random.choices(("active", "pending", "expired", "completed", "terminated"), weights=(39, 9, 16, 22, 14))[0]
        status = {"completed": complete, "terminated": terminated}.get(state, active)
        if state == "pending":
            start = relative(random.randint(6, 113))
            end = start + timedelta(days=random.choice((95, 185, 365, 720)))
        elif state == "active":
            start = relative(-random.randint(6, 590))
            end = relative(random.randint(0, 420))
        elif state == "expired":
            end = relative(-random.randint(5, 405))
            start = max(relative(-730), end - timedelta(days=random.choice((42, 96, 184, 365))))
        else:
            start = relative(-random.randint(95, 692))
            end = start + timedelta(days=random.choice((64, 120, 185, 368, 548, 725)))
        if kind == sale and state != "expired" and random.random() < .34:
            end = None
        eligible = [
            item for item in properties
            if item.created_at <= start and compatible(kind, item.type)
            and (state not in ("active", "pending") or item.status != PropertyStatus.ARCHIVED)
        ]
        prop = random.choice(eligible)
        cutoff = None
        if state in ("completed", "terminated"):
            cutoff = min(end or today, today - timedelta(days=random.randint(3, 87)))
            cutoff = max(cutoff, start)
        add(prop, random.choice(parties), kind, status, start, end, cutoff=cutoff)

    return plans


def description(random, contract, day, cadence):
    if random.random() > .41:
        return None
    month = f"{MONTHS[day.month - 1]} {day.year} года"
    if contract.type == ContractType.REAL_ESTATE_RENT:
        if cadence == 1:
            return f"Арендная плата за {month}."
        return f"Периодическая арендная плата, расчёт на {day:%d.%m.%Y}."
    if contract.type == ContractType.LAND_RENT:
        return f"Плата за пользование земельным участком, {month}."
    if contract.type == ContractType.SALE:
        return f"Часть цены по договору купли-продажи, {month}."
    return random.choice((f"Платёж по графику за {month}.", f"Начисление по условиям договора от {day:%d.%m.%Y}."))


def make_accruals(random, plans):
    entries = []
    for plan in plans:
        contract = plan.contract
        if plan.cutoff < contract.start_date:
            continue
        if plan.cadence:
            days = [contract.start_date]
            day_in_month = min(contract.start_date.day, 25)
            step = plan.cadence
            while (day := shift_month(contract.start_date, step) + timedelta(days=day_in_month - 1)) <= plan.cutoff:
                days.append(day)
                step += plan.cadence
            shares = [plan.installment] * len(days)
        else:
            days = [contract.start_date + timedelta(days=offset) for offset in (0, 36, 94)]
            shares = [amount(contract.amount * share) for share in (Decimal(".37"), Decimal(".34"))]
            shares.append(contract.amount - sum(shares))
        left = contract.amount
        for index, (day, share) in enumerate(zip(days, shares)):
            if day > plan.cutoff or left <= 0:
                break
            value = min(left, share)
            left -= value
            entries.append((Accrual(
                contract_id=contract.id,
                amount=value,
                accrual_date=day,
                due_date=day + timedelta(days=random.randint(10, 28)),
                description=description(random, contract, day, plan.cadence),
            ), plan.arrears and index == 0))
    return entries


def make_payments(random, entries, today):
    for accrual, arrears in entries:
        if arrears:
            outcome = "partial"
        elif accrual.due_date >= today:
            outcome = random.choices(("full", "partial", "unpaid"), (23, 14, 63))[0]
        else:
            full_weight = 70 + (5 if accrual.accrual_date.month in (3, 6, 9, 12) else 0)
            outcome = random.choices(("full", "partial", "unpaid"), (full_weight, 15, 85 - full_weight))[0]
        if outcome == "unpaid":
            continue
        first = min(today, accrual.accrual_date + timedelta(days=random.randint(3, 34)))
        split = outcome == "full" and first < today - timedelta(days=4) and random.random() < .22
        if outcome == "partial" or split:
            paid = amount(accrual.amount * Decimal(random.choice((".20", ".35", ".45", ".55", ".70", ".80"))))
        else:
            paid = accrual.amount
        yield Payment(accrual_id=accrual.id, amount=paid, payment_date=first)
        if split:
            second = min(today, first + timedelta(days=random.randint(5, 35)))
            yield Payment(accrual_id=accrual.id, amount=accrual.amount - paid, payment_date=second)


def populate(db: Session, today: date) -> tuple[int, int, int, int, int] | None:
    lock_dataset(db)
    if any(db.scalar(select(model.id).limit(1)) is not None for model in TABLES):
        return None

    random = Random(2814)
    properties, parties = make_properties(random, today), make_parties()
    db.add_all(properties + parties)
    db.flush()

    plans = make_contracts(random, today, properties, parties)
    db.add_all(plan.contract for plan in plans)
    db.flush()
    for prop, status in db.execute(select(Property, property_status(today))):
        prop.status = PropertyStatus(status)

    entries = make_accruals(random, plans)
    db.add_all(accrual for accrual, _ in entries)
    db.flush()
    payments = list(make_payments(random, entries, today))
    db.add_all(payments)
    return len(properties), len(parties), len(plans), len(entries), len(payments)


def main() -> None:
    parser = argparse.ArgumentParser(description="Заполнить пустую схему вымышленными муниципальными данными.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=local_today(), help="Опорная дата в формате ГГГГ-ММ-ДД.")
    today = parser.parse_args().as_of
    if not date(3, 1, 1) <= today <= local_today():
        parser.error("Укажите опорную дату от 0003 года до текущего дня включительно.")
    with Session(get_engine()) as db, db.begin():
        counts = populate(db, today)
    if counts:
        print("Добавлены объекты, контрагенты, договоры, начисления и платежи:", *counts)
    else:
        print("В проектной схеме уже есть записи. Посев пропущен.")


if __name__ == "__main__":
    main()
