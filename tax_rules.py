"""УСН «Доходы»: cash-basis register and cumulative tax, amounts in kopeks.

2026 reference rules: NK RF 346.15, 346.17, 346.21, 430; FNS order
07.11.2023 ЕА-7-3/816@. No tax filing, bank payment or ENS balance is inferred.
"""
from __future__ import annotations

import calendar
import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

RULES_YEAR = 2026
FIXED_ANNUAL_KOP = 5_739_000
EXTRA_CAP_KOP = 32_181_800
THRESHOLD_KOP = 30_000_000
IMPORT_COLUMNS = ('operation_id', 'date', 'document', 'kind', 'amount', 'source_key',
                  'original_id', 'description', 'tax_year')
KINDS = {'income', 'transit', 'agent_fee', 'refund', 'tax_payment', 'reverse', 'exclude'}


def money_kop(value: str) -> int:
    raw = str(value).strip().replace(' ', '').replace('\u00a0', '').replace(',', '.')
    try:
        number = Decimal(raw)
        if not number.is_finite() or number < 0 or number > Decimal('999999999999.99'):
            raise ValueError('Сумма должна быть неотрицательной и конечной.')
        scaled = number * 100
        if scaled != scaled.to_integral_value():
            raise ValueError('В сумме допускаются только две цифры после запятой.')
        return int(scaled)
    except InvalidOperation as exc:
        raise ValueError('Некорректная сумма в рублях.') from exc


def parse_date(value: str) -> date:
    raw = value.strip()
    try:
        if len(raw) == 10 and raw[2] == '.' and raw[5] == '.':
            return date(int(raw[6:]), int(raw[3:5]), int(raw[:2]))
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('Дата должна быть ДД.ММ.ГГГГ или ГГГГ-ММ-ДД.') from exc


def rubles(kop: int) -> int:
    return int((Decimal(kop) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def format_money(kop: int) -> str:
    return f'{Decimal(kop) / 100:,.2f}'.replace(',', ' ')


def period_end(year: int, quarter: int) -> date:
    if quarter not in (1, 2, 3, 4):
        raise ValueError('Квартал: 1–4.')
    month = quarter * 3
    return date(year, month, calendar.monthrange(year, month)[1])


def additional_contribution(income_kop: int, year: int) -> int:
    if year != RULES_YEAR:
        raise ValueError('Правила страховых взносов проверены только для 2026 года.')
    return min(EXTRA_CAP_KOP, int((Decimal(max(0, income_kop - THRESHOLD_KOP)) / 100)
                               .quantize(Decimal('1'), rounding=ROUND_HALF_UP)))


def calculate_tax(entries: list[dict], allocations: list[dict], *, year: int,
                  quarter: int, rate: Decimal, start: date, as_of: date,
                  has_workers: bool = False) -> dict:
    if year != RULES_YEAR:
        raise ValueError('Для этого года налоговые правила ещё не проверены.')
    if has_workers:
        raise ValueError('Этот модуль рассчитан на ИП без выплат обычным физлицам.')
    if not Decimal('0') <= rate <= Decimal('6'):
        raise ValueError('Ставка УСН «Доходы» должна быть от 0 до 6%.')
    end = period_end(year, quarter)
    cutoff = min(end, as_of)
    begin = max(start, date(year, 1, 1))
    selected = [r for r in entries if begin <= r['tax_date'] <= cutoff]
    income = sum(int(r['income_kop']) for r in selected)
    raw_tax = Decimal(max(0, income)) * rate / 10000
    gross_tax = int(raw_tax.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    eligible = sum(int(r['allocated_kop']) for r in allocations
                   if r['tax_year'] == year and r['effective_date'] <= cutoff)
    # Calculate tax after deduction before final rounding; do not round each
    # contribution separately or subtract bank fees from the taxable income.
    net_tax = int(max(Decimal(0), raw_tax - Decimal(eligible) / 100)
                  .quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    previous_net = 0
    if quarter > 1:
        previous_net = calculate_tax(entries, allocations, year=year, quarter=quarter-1,
                                     rate=rate, start=start, as_of=as_of)['net_tax_rub']
    tax_paid = sum(int(r.get('tax_paid_kop') or 0) for r in entries
                   if r.get('tax_year') == year and r['tax_date'] <= as_of)
    delta = net_tax - previous_net
    return dict(year=year, quarter=quarter, end=end, cutoff=cutoff,
                closed=as_of > end, income_kop=income, gross_tax_rub=gross_tax,
                eligible_deduction_kop=eligible, net_tax_rub=net_tax,
                advance_due_rub=max(0, delta), advance_reduction_rub=max(0, -delta),
                previously_accrued_rub=previous_net, tax_paid_kop=tax_paid,
                balance_kop=net_tax * 100 - tax_paid,
                estimated_extra_kop=additional_contribution(max(0, income), year))


def validate_allocation(*, kind: str, source_year: int, tax_year: int,
                        due_kop: int, allocated_kop: int, other_allocated_kop: int,
                        effective_date: date) -> None:
    if kind not in {'fixed', 'extra'}:
        raise ValueError('Вид взноса: fixed или extra.')
    if tax_year != RULES_YEAR or source_year not in {RULES_YEAR-1, RULES_YEAR}:
        raise ValueError('Неподдерживаемый год взноса/налога.')
    if kind == 'fixed' and tax_year != source_year:
        raise ValueError('Фиксированные взносы относятся к своему году.')
    if tax_year not in {source_year, source_year+1} or effective_date.year != tax_year:
        raise ValueError('Неверный год уменьшения налога.')
    if min(due_kop, allocated_kop, other_allocated_kop) < 0:
        raise ValueError('Взносы не могут быть отрицательными.')
    if allocated_kop + other_allocated_kop > due_kop:
        raise ValueError('Взнос уже использован либо уменьшение превышает обязательство.')
    if source_year == 2026 and due_kop > (FIXED_ANNUAL_KOP if kind == 'fixed' else EXTRA_CAP_KOP):
        raise ValueError('Сумма превышает установленный на 2026 год предел взноса.')


def parse_import(data: bytes) -> list[dict]:
    if len(data) > 2_000_000:
        raise ValueError('CSV должен быть не больше 2 МБ.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('Сохраните CSV в UTF-8.') from exc
    reader = csv.DictReader(io.StringIO(text), delimiter=';')
    if tuple(reader.fieldnames or ()) != IMPORT_COLUMNS:
        raise ValueError('Неверные колонки. Скачайте шаблон CSV в разделе бухгалтерии.')
    rows = []
    keys = set()
    for number, row in enumerate(reader, 2):
        if len(rows) >= 5000:
            raise ValueError('Не более 5000 операций за один импорт.')
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f'Строка {number}: неверное число полей.')
        row = {k: v.strip() for k, v in row.items()}
        if not row['operation_id'] or len(row['operation_id']) > 120 or row['operation_id'] in keys:
            raise ValueError(f'Строка {number}: отсутствует или повторяется operation_id.')
        if not row['document'] or len(row['document']) > 500:
            raise ValueError(f'Строка {number}: укажите первичный документ (до 500 символов).')
        if len(row['description']) > 1000 or len(row['source_key']) > 160 or len(row['original_id']) > 120:
            raise ValueError(f'Строка {number}: слишком длинное поле.')
        if row['kind'] not in KINDS:
            raise ValueError(f'Строка {number}: неизвестный вид операции.')
        row['tax_date'] = parse_date(row.pop('date'))
        row['amount_kop'] = money_kop(row.pop('amount'))
        try:
            row['tax_year'] = int(row['tax_year'] or row['tax_date'].year)
        except ValueError as exc:
            raise ValueError(f'Строка {number}: tax_year должен быть годом налога.') from exc
        if row['amount_kop'] == 0 and row['kind'] != 'exclude':
            raise ValueError(f'Строка {number}: сумма должна быть больше нуля.')
        keys.add(row['operation_id'])
        rows.append(row)
    if not rows:
        raise ValueError('В файле нет операций.')
    return rows


def import_template() -> bytes:
    return ('\ufeff' + ';'.join(IMPORT_COLUMNS) + '\n').encode('utf-8')
