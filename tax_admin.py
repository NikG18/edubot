"""Telegram-only accounting admin UI. No real bank or FNS actions are performed."""
from __future__ import annotations
import csv
import io
import logging
from datetime import datetime
from secrets import token_hex

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import tax_accounting as accounting
from tax_rules import (RULES_YEAR, format_money, import_template, parse_date, parse_import, money_kop)
from tax_export import export_bundle


class TaxStates(StatesGroup):
    entry=State()
    import_csv=State()
    contribution=State()
    bank=State()
    reconciliation=State()
    guided=State()
    preview=State()


HELP = (
    '📒 Бухгалтерия: УСН 6%, без работников, с 07.08.2026.\n\n'
    '• Собственные занятия/абонементы: полная полученная сумма до комиссии банка.\n'
    '• Платежи репетиторам: транзит; доход ИП — только вознаграждение.\n'
    '• Для эквайринга берите дату зачисления и документ из банковской выписки.\n'
    '• Для комиссии — дату и документ фактического удержания/зачёта. '
    'Само создание отчёта ещё не подтверждает получение дохода.\n'
    '• Учёт взносов отдельно от уплаты: вносите только разрешённое уменьшение, '
    'не использованное ранее.\n\n'
    'CSV: UTF-8, разделитель «;». Шаблон содержит названия полей. '
    'Виды: income (свой доход), transit (чужие деньги), agent_fee (комиссия из отчёта), '
    'refund (возврат), tax_payment (погашение УСН по ЕНС), reverse (исправление), '
    'exclude (исключить источник с объяснением).\n'
    'source_key — ключ из списка сверки, original_id — operation_id первоначального дохода '
    'для refund/reverse. tax_year — год налога (для УСН за 2026, уплаченного в 2027, укажите 2026). Возврат вводится положительной суммой: знак ставится автоматически.\n'
    'Платежи и удержания можно загрузить пакетом; повторный импорт не дублирует записи.\n\n'
    'Поддерживаются правила 2026 года. НДС, зарплата, торговый сбор, другие режимы и '
    'подача отчётности в ФНС в этот модуль не входят.'
)


def keyboard():
    rows=[('📊 Налог и авансы','tax_summary'),('🔎 Операции на сверке','tax_pending_0'),
          ('📥 Импорт CSV','tax_import'),('➕ Внести операцию','tax_entry'),
          ('📋 Журнал операций','tax_entries_0'),
          ('📄 Выгрузить КУДиР','tax_export'),('🧾 Взносы и уменьшение','tax_contributions'),
          ('🏦 Реквизиты счёта','tax_bank'),('✅ Завершить сверку','tax_reconcile'),
          ('ℹ️ Правила и шаблон','tax_help'),('🔙 В админ-панель','admin_panel_open')]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t,callback_data=c)] for t,c in rows])


def back():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 Бухгалтерия',callback_data='admin_tax')]])


KIND_LABELS = {
    'income': 'Собственный доход', 'transit': 'Транзит', 'agent_fee': 'Вознаграждение',
    'refund': 'Возврат', 'tax_payment': 'Погашение УСН', 'reverse': 'Сторно',
    'exclude': 'Исключение из сверки',
}
MANUAL_KINDS = ('income', 'transit', 'tax_payment')
PAGE_SIZE = 8


def with_back(*rows, target='admin_tax'):
    keys = [[InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows if row]
    keys.append([InlineKeyboardButton(text='🔙 Назад', callback_data=target)])
    return InlineKeyboardMarkup(inline_keyboard=keys)


def pagination(prefix, offset, count):
    result = []
    if offset:
        result.append(('← Предыдущая', f'{prefix}_{max(0, offset - PAGE_SIZE)}'))
    if offset + PAGE_SIZE < count:
        result.append(('Следующая →', f'{prefix}_{offset + PAGE_SIZE}'))
    return result


def row_from_fields(operation_id, when, document, kind, amount, *, source='', original='', description=''):
    out = io.StringIO()
    csv.writer(out, delimiter=';').writerow([
        operation_id, when, document, kind, amount, source, original, description, RULES_YEAR,
    ])
    return parse_import(import_template() + out.getvalue().encode('utf-8'))[0]


def form_fields(text, expected):
    fields = [value.strip() for value in next(csv.reader([text], delimiter=';'), [])]
    if len(fields) != expected or not all(fields):
        raise ValueError(f'Заполните {expected} полей через «;».')
    return fields


async def build_guided_row(context, text):
    if 'source' in context:
        ref = context['source']
        source = await accounting.review_source(ref)
        if not source or source.get('posted_operation'):
            raise ValueError('Источник уже обработан или недоступен. Обновите список сверки.')
        if context.get('exclude'):
            document, reason = form_fields(text, 2)
            return row_from_fields(f'bot-source:{ref}', datetime.now(accounting.db.MSK).date(), document,
                                   'exclude', '0', source=source['source_key'], description=reason)
        kind = accounting.SOURCE_ENTRY_KINDS.get(source['source_kind'], {}).get(source['role'])
        if not kind or source['gross_kop'] <= 0:
            raise ValueError('Для источника не подтверждён поставщик. Сначала сверьте документы.')
        original = source.get('original_operation') if kind == 'refund' else None
        if kind == 'refund' and not original:
            raise ValueError('Сначала внесите исходное поступление.')
        when, document = form_fields(text, 2)
        return row_from_fields(f'bot-source:{ref}', when, document, kind,
                               format_money(source['gross_kop']), source=source['source_key'],
                               original=original or '', description=source['description'][:1000])

    if 'entry' in context:
        original = await accounting.get_entry(context['entry'], RULES_YEAR)
        if not original:
            raise ValueError('Исходная операция не найдена.')
        kind = context['kind']
        if kind == 'reverse':
            operation_id, document, description = form_fields(text, 3)
            when, amount = original['tax_date'], format_money(original['gross_kop'])
        else:
            operation_id, when, document, amount, description = form_fields(text, 5)
        return row_from_fields(operation_id, when, document, kind, amount,
                               original=original['operation_id'], description=description)

    operation_id, when, document, amount, description = form_fields(text, 5)
    return row_from_fields(operation_id, when, document, context['kind'], amount, description=description)


def preview_text(row):
    lines = ['Проверьте операцию перед сохранением:',
             f"Вид: {KIND_LABELS[row['kind']]}",
             f"ID: {row['operation_id']}",
             f"Дата: {row['tax_date']:%d.%m.%Y}",
             f"Сумма: {format_money(row['amount_kop'])} ₽",
             f"Документ: {row['document']}",
             f"Описание: {row['description']}"]
    if row['source_key']:
        lines.append(f"Источник: {row['source_key']}")
    if row['original_id']:
        lines.append(f"Исходная операция: {row['original_id']}")
    return '\n'.join(lines)


def install_telegram_tax_accounting(app):
    legacy=app.legacy
    if getattr(legacy,'_tax_accounting_installed',False):
        return
    legacy._document_input_states = set(getattr(legacy, '_document_input_states', ())) | {TaxStates.import_csv.state}
    original=legacy.admin_actions_keyboard
    def admin_keyboard():
        kb=original()
        kb.inline_keyboard.insert(max(0,len(kb.inline_keyboard)-1),[
            InlineKeyboardButton(text='📒 Бухгалтерия / КУДиР',callback_data='admin_tax')])
        return kb
    legacy.admin_actions_keyboard=admin_keyboard

    async def allowed(source):
        message = getattr(source, 'message', source)
        chat = getattr(message, 'chat', None)
        if source.from_user.id == legacy.ADMING_ID and (chat is None or chat.type == 'private'):
            return True
        if hasattr(source,'data'):
            await legacy.safe_answer(source,'⛔ Бухгалтерия доступна администратору в личном чате',show_alert=True)
        else:
            await source.answer('⛔ Бухгалтерия доступна администратору в личном чате')
        return False

    @legacy.dp.callback_query(F.data=='admin_tax')
    async def menu(call,state):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        await state.clear()
        try:
            profile = await accounting.get_profile()
            await accounting.sync_sources()
            count,_ = await accounting.unresolved(limit=1)
            text = (f"📒 УСН «Доходы» {profile['rate']:g}%\n"
                    f"Начало учёта: {profile['start_date']:%d.%m.%Y}\n"
                    f"Операций на сверке: {count}\n"
                    f"Взносы: {'настроены' if profile['contributions_reviewed'] else 'не настроены'}")
        except Exception:
            logging.exception('Tax source list unavailable')
            text = '📒 Бухгалтерия\nНе удалось загрузить сводку. Можно открыть нужный раздел или повторить попытку.'
        await call.message.edit_text(text, reply_markup=keyboard(), parse_mode=None)

    async def accounting_errors(handler, event, data):
        command = event.data or ''
        if command != 'admin_tax' and not command.startswith('tax_'):
            return await handler(event, data)
        if not await allowed(event):
            return None
        try:
            state = data.get('state')
            if state and (command in {'tax_summary', 'tax_help', 'tax_export'}
                          or command.startswith(('tax_quarter_', 'tax_pending_', 'tax_entries_', 'tax_item_', 'tax_src_'))):
                await state.clear()
            return await handler(event, data)
        except ValueError as exc:
            await event.message.answer(str(exc), reply_markup=back(), parse_mode=None)
        except Exception:
            logging.exception('Accounting action failed: %s', command)
            await event.message.answer('Не удалось выполнить действие. Откройте раздел заново или повторите попытку.',
                                       reply_markup=back(), parse_mode=None)
    legacy.dp.callback_query.outer_middleware(accounting_errors)

    @legacy.dp.callback_query(F.data=='tax_summary')
    async def tax_summary(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        buttons=[[InlineKeyboardButton(text=t,callback_data=f'tax_quarter_{q}')]
                 for q,t in ((1,'I квартал'),(2,'Полугодие'),(3,'9 месяцев'),(4,'Год'))]
        buttons+=back().inline_keyboard
        await call.message.edit_text('Выберите период 2026 года:',reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @legacy.dp.callback_query(F.data.regexp(r'^tax_quarter_[1-4]$'))
    async def tax_period(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        try:
            profile,r=await accounting.summary(2026,int(call.data.rsplit('_',1)[1]))
            text=(f"📊 УСН 2026, по {r['cutoff']:%d.%m.%Y}\n"
                  f"{'Период закрыт' if r['closed'] else 'Период ещё не завершён'}\n"
                  f"{'Сверка завершена' if r['complete'] else 'ПРЕДВАРИТЕЛЬНО: сверка/настройки не завершены'}\n\n"
                  f"Доход нарастающим итогом: {format_money(r['income_kop'])} ₽\n"
                  f"Налог до уменьшения: {r['gross_tax_rub']} ₽\n"
                  f"Разрешённые взносы для уменьшения: {format_money(r['eligible_deduction_kop'])} ₽\n"
                  f"Налог после уменьшения: {r['net_tax_rub']} ₽\n"
                  f"Начислено за предыдущие периоды: {r['previously_accrued_rub']} ₽\n"
                  f"Аванс/налог за выбранный период к начислению: {r['advance_due_rub']} ₽\n"
                  f"К уменьшению ранее начисленного: {r['advance_reduction_rub']} ₽\n\n"
                  f"Учтено платежей УСН за 2026 на сегодня: {format_money(r['tax_paid_kop'])} ₽\n"
                  f"Разница налога и этих платежей: {format_money(r['balance_kop'])} ₽\n"
                  'Это расчётная разница, не сальдо ЕНС.\n\n'
                  f"Оценка допвзноса 1% по учтённому доходу: {format_money(r['estimated_extra_kop'])} ₽\n"
                  'В уменьшение он попадает только после внесения в «Взносы».\n'
                  f"Операций на сверке: {r['unresolved']}\n\n"
                  + ('Доход достиг 20 млн ₽: отдельно проверьте обязанности по НДС; модуль НДС не считает.\n\n'
                     if r['income_kop'] >= 2_000_000_000 else '') +
                  'Авансы: обычно до 28 апреля/июля/октября; уведомления — до 25-го. '
                  'Годовой налог ИП — до 28 апреля следующего года, декларация — до 25 апреля. '
                  'Выходные и праздничные дни переносят сроки.')
            await call.message.edit_text(text,reply_markup=back(),parse_mode=None)
        except ValueError as exc:
            await call.message.answer(str(exc),reply_markup=back(),parse_mode=None)

    @legacy.dp.callback_query(F.data.regexp(r'^tax_pending_\d{1,9}$'))
    async def pending(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        await accounting.sync_sources()
        offset = int(call.data.rsplit('_', 1)[1])
        count, rows = await accounting.unresolved(limit=PAGE_SIZE, offset=offset)
        if offset and not rows:
            offset = max(0, (count - 1) // PAGE_SIZE) * PAGE_SIZE
            count, rows = await accounting.unresolved(limit=PAGE_SIZE, offset=offset)
        lines = [f'🔎 Операции на сверке: {count}']
        buttons = []
        if rows:
            lines.append('Откройте операцию и сравните её с выпиской или документом.')
        for n, row in enumerate(rows, offset + 1):
            amount = format_money(row['gross_kop'])
            lines.append(f"{n}. {row['description'][:180]} · {amount} ₽\n{row['source_key']}")
            buttons.append([(f"Открыть {n} · {amount} ₽", f"tax_src_{row['review_ref']}")])
        buttons.append(pagination('tax_pending', offset, count))
        await call.message.edit_text('\n\n'.join(lines), reply_markup=with_back(*buttons), parse_mode=None)

    @legacy.dp.callback_query(F.data.regexp(r'^tax_src_[a-f0-9]{32}$'))
    async def source_card(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        ref = call.data.rsplit('_', 1)[1]
        row = await accounting.review_source(ref)
        if not row:
            raise ValueError('Источник больше не доступен.')
        text = (f"Источник: {row['source_key']}\n{row['description'][:500]}\n"
                f"Сумма: {format_money(row['gross_kop'])} ₽")
        actions = []
        if row.get('posted_operation'):
            text += f"\nУже обработано: {row['posted_operation']}"
        else:
            kind = accounting.SOURCE_ENTRY_KINDS.get(row['source_kind'], {}).get(row['role'])
            if kind and row['gross_kop'] > 0 and (kind != 'refund' or row.get('original_operation')):
                text += f"\nВид: {KIND_LABELS[kind]}\nУкажите дату и документ по фактической операции."
                actions.append([('Внести по документу', f'tax_accept_{ref}')])
            elif kind == 'refund':
                text += '\nСначала внесите исходное поступление.'
            else:
                text += '\nНедостаточно данных о поставщике. Нужна сверка с документами.'
            actions.append([('Исключить с объяснением', f'tax_exclude_{ref}')])
        await call.message.edit_text(text, reply_markup=with_back(*actions, target='tax_pending_0'), parse_mode=None)

    @legacy.dp.callback_query(F.data.regexp(r'^tax_entries_\d{1,9}$'))
    async def entries(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        count, offset, rows = await accounting.entries_page(RULES_YEAR, limit=PAGE_SIZE,
                                                          offset=int(call.data.rsplit('_', 1)[1]))
        text = [f'📋 Журнал операций: {count}']
        keys = []
        for row in rows:
            n = row['id']
            text.append(f"№{n} · {row['tax_date']:%d.%m.%Y} · {KIND_LABELS[row['kind']]} · "
                        f"{format_money(row['gross_kop'])} ₽\nID: {row['operation_id']}")
            keys.append([(f'Открыть №{n}', f'tax_item_{n}_{offset}')])
        keys.append(pagination('tax_entries', offset, count))
        await call.message.edit_text('\n\n'.join(text), reply_markup=with_back(*keys), parse_mode=None)

    @legacy.dp.callback_query(F.data.regexp(r'^tax_item_\d{1,18}_\d{1,9}$'))
    async def entry_card(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        _, _, entry_id, offset = call.data.split('_')
        row = await accounting.get_entry(int(entry_id), RULES_YEAR)
        if not row:
            raise ValueError('Запись не найдена.')
        text = (f"Операция №{entry_id} · {KIND_LABELS[row['kind']]}\n"
                f"ID: {row['operation_id']}\nДата: {row['tax_date']:%d.%m.%Y}\n"
                f"Документ: {row['document']}\nСумма: {format_money(row['gross_kop'])} ₽\n"
                f"В налоговую базу: {format_money(row['income_kop'])} ₽\n"
                f"Учтено в погашение УСН: {format_money(row['tax_paid_kop'])} ₽\n"
                f"Описание: {row['description'] or '—'}\n"
                f"Источник: {row['source_key'] or '—'}\nИсходная операция: {row['original_operation'] or '—'}")
        actions = []
        if row['kind'] in {'income', 'agent_fee'}:
            actions.append([('Внести возврат', f'tax_refund_{entry_id}')])
        if row['kind'] in {'income', 'agent_fee', 'refund', 'tax_payment', 'transit'}:
            actions.append([('Сторнировать операцию', f'tax_reverse_{entry_id}')])
        await call.message.edit_text(text, reply_markup=with_back(*actions, target=f'tax_entries_{offset}'),
                                     parse_mode=None)

    @legacy.dp.callback_query(F.data=='tax_help')
    async def help_page(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        await call.message.edit_text(HELP,reply_markup=back(),parse_mode=None)
        await call.message.answer_document(BufferedInputFile(import_template(),filename='tax_import_template.csv'),
                                           caption='Заполните строки по выписке/документам. Здесь нет реальных операций.')

    @legacy.dp.callback_query(F.data=='tax_export')
    async def export(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        try:
            data,complete,count=await export_bundle(2026,call.from_user.id)
            await call.message.answer_document(BufferedInputFile(data,filename='KUDIR_2026.zip'),
                caption=f"КУДиР, реестр операций и расчёт налога. {'Сверено.' if complete else 'Предварительно.'} На сверке: {count}.",
                reply_markup=back(),parse_mode=None)
        except Exception:
            logging.exception('Tax export failed')
            await call.message.answer('Выгрузка не удалась. Записи учёта сохранены.',reply_markup=back())

    prompts={
        'tax_import':(TaxStates.import_csv,'Пришлите CSV по шаблону (UTF-8, «;», до 2 МБ). '
                      'Импорт подтверждённых операций выполняется целиком; суммы — до комиссии банка.'),
        'tax_entry':(TaxStates.entry,'Введите одну операцию строкой:\n'
                    'operation_id;дата;документ;kind;сумма;source_key;original_id;описание;tax_year\n\n'
                    'Пример своего дохода вне бота:\n'
                    'bank-001;25.09.2026;Выписка №15;income;2500;;;Оплата занятия;2026\n'
                    'Для платежа из бота заполните source_key, например payment:12345. '
                    'Для налога: tax_payment, без source_key. Для возврата: refund и ID исходной записи.'),
        'tax_contributions':(TaxStates.contribution,
                    'Внесите распределение взноса для уменьшения УСН:\n'
                    'год_взноса;fixed/extra;обязательство_руб;использовано_вне_бота_руб;уменьшение_2026_руб;дата_доступности;документ\n\n'
                    'fixed — фиксированный взнос, extra — дополнительный 1%. '
                    'Укажите фактическое обязательство за ваш неполный год по данным ФНС, '
                    'не автоматически полные 57 390 ₽. Вне бота — уже использованная часть в другом году/режиме. '
                    'Каждая дата — отдельная часть уменьшения. Повтор на ту же дату заменяет эту часть с сохранением истории; разные даты суммируются.'),
        'tax_bank':(TaxStates.bank,'Введите номер расчётного счёта и название банка для титульного листа КУДиР.'),
        'tax_reconcile':(TaxStates.reconciliation,
                    'Укажите дату, по которую сверили учёт с банковской выпиской и кассой, '
                    'включили доходы вне бота, возвраты, фактически удержанную комиссию и погашения УСН по ЕНС. '
                    'Например: 25.09.2026. Это отметка о вашей сверке, не автоматическая проверка банка.'),
    }
    @legacy.dp.callback_query(F.data.in_(set(prompts)))
    async def prompt(call,state):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        target,text=prompts[call.data]
        await state.clear()
        kb = back()
        if call.data == 'tax_entry':
            kb = with_back(*[[(label, f'tax_new_{kind}')] for kind, label in KIND_LABELS.items()
                             if kind in MANUAL_KINDS],
                           [('🔎 Операции на сверке', 'tax_pending_0'), ('📋 Журнал', 'tax_entries_0')])
            text = ('Выберите вид операции. Возврат или сторно можно внести из карточки '
                    'исходной операции в журнале. Платежи бота разбираются из списка сверки.\n\n'
                    'Для расширенного ввода можно прислать одну строку по шаблону CSV.')
        elif call.data == 'tax_import':
            kb = with_back([('Скачать шаблон', 'tax_template')])
        elif call.data == 'tax_contributions':
            profile, _, allocations = await accounting.data_for_year(RULES_YEAR)
            if not allocations:
                kb = with_back([('Считать без уменьшения', 'tax_no_deductions')])
            else:
                total = sum(r['allocated_kop'] for r in allocations)
                text += f'\n\nВнесено уменьшение: {format_money(total)} ₽.'
                for r in sorted(allocations, key=lambda value: value['effective_date'])[-12:]:
                    text += (f"\n{r['effective_date']:%d.%m.%Y} · {r['source_year']} {r['kind']}: "
                             f"{format_money(r['allocated_kop'])} ₽")
                text += '\nЧтобы обнулить часть уменьшения, введите 0 с её исходной датой.'
        elif call.data == 'tax_bank':
            profile = await accounting.get_profile()
            text = f"Сохранённые реквизиты: {profile['bank_details'] or 'не заполнены'}\n\n{text}"
        elif call.data == 'tax_reconcile':
            await accounting.sync_sources()
            count, _ = await accounting.unresolved(limit=1)
            if count:
                await call.message.edit_text(f'На сверке ещё {count} операций. Сначала разберите их.',
                                             reply_markup=with_back([('Открыть операции', 'tax_pending_0')]),
                                             parse_mode=None)
                return
            profile = await accounting.get_profile()
            through = profile.get('reconciled_through')
            if through:
                text = f"Отмечена сверка по {through:%d.%m.%Y}.\n\n{text}"
        await state.set_state(target)
        await call.message.edit_text(text,reply_markup=kb,parse_mode=None)

    @legacy.dp.callback_query(F.data=='tax_template')
    async def template(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        await call.message.answer_document(BufferedInputFile(import_template(),filename='tax_import_template.csv'),
                                           caption='Шаблон CSV. Заполните его и отправьте в этот чат.')

    @legacy.dp.callback_query(F.data.regexp(
        r'^tax_(?:(?:accept|exclude)_[a-f0-9]{32}|new_(?:income|transit|tax_payment)|(?:refund|reverse)_\d{1,18})$'
    ))
    async def guided_prompt(call, state):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        _, action, value = call.data.split('_', 2)
        if action in {'accept', 'exclude'}:
            context = {'source': value, 'exclude': action == 'exclude'}
            target = f'tax_src_{value}'
            fields = 'документ;причина исключения' if context['exclude'] else 'дата по документу;номер и название документа'
            text = ('Исключайте источник только с объяснением, почему он не должен попасть в учёт.\n'
                    if context['exclude'] else 'Введите фактическую дату зачисления, возврата или удержания.\n')
        elif action in {'refund', 'reverse'}:
            context = {'entry': int(value), 'kind': action}
            target = f'tax_item_{value}_0'
            fields = ('ID новой операции;документ;причина исправления' if action == 'reverse'
                      else 'ID новой операции;дата возврата;документ;сумма;описание')
            text = KIND_LABELS[action] + '.\n'
        else:
            context = {'kind': value}
            target = 'tax_entry'
            fields = 'ID операции;дата;документ;сумма в рублях;описание'
            text = KIND_LABELS[value] + '.\n'
            if value == 'tax_payment':
                text += 'Укажите погашение именно УСН за 2026 год по ЕНС.\n'
        await state.clear()
        await state.set_data({'tax_context': context, 'tax_back': target})
        await state.set_state(TaxStates.guided)
        await call.message.edit_text(text + f'Пришлите одну строку:\n{fields}',
                                     reply_markup=with_back(target=target), parse_mode=None)

    @legacy.dp.callback_query(F.data.regexp(r'^tax_save_[a-f0-9]{16}$'))
    async def post_draft(call, state):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        values = await state.get_data()
        if await state.get_state() != TaxStates.preview.state or call.data != values.get('tax_confirm'):
            await call.message.answer('Подтверждение устарело. Откройте операцию заново.', reply_markup=back())
            return
        row = dict(values['tax_row'])
        row['tax_date'] = parse_date(row['tax_date'])
        try:
            result = await accounting.import_entries([row], call.from_user.id)
        except ValueError as exc:
            await call.message.answer(str(exc), reply_markup=with_back(target=values['tax_back']), parse_mode=None)
            return
        await state.clear()
        note = 'Операция записана.' if result['inserted'] else 'Эта операция уже была записана.'
        await call.message.edit_text(note, reply_markup=with_back([('📋 Журнал', 'tax_entries_0')],
                                    [('🔎 К сверке', 'tax_pending_0')]), parse_mode=None)

    @legacy.dp.callback_query(F.data=='tax_no_deductions')
    async def no_deductions(call,state):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        try:
            await accounting.update_profile(actor_id=call.from_user.id,no_deductions=True)
            await state.clear()
            await call.message.edit_text('Уменьшение пока 0 ₽. Взносы можно добавить позже.',reply_markup=back())
        except ValueError as exc:
            await call.message.answer(str(exc),reply_markup=back(),parse_mode=None)

    @legacy.dp.message(
        lambda message: (message.text or '').split(maxsplit=1)[0:1] != ['/start']
        and not (message.text or '').startswith('/start@'),
        StateFilter(TaxStates.entry, TaxStates.import_csv, TaxStates.contribution,
                    TaxStates.bank, TaxStates.reconciliation, TaxStates.guided, TaxStates.preview),
    )
    async def save(message,state):
        if not await allowed(message): return
        current=await state.get_state()
        text=(message.text or '').strip()
        if text.lower() in {'отмена','назад','/cancel'}:
            await state.clear();await message.answer('Ввод отменён.',reply_markup=back());return
        try:
            if current == TaxStates.preview.state:
                await message.answer('Подтвердите операцию кнопкой под предварительным просмотром '
                                     'или напишите «отмена».')
                return
            if current == TaxStates.guided.state:
                values = await state.get_data()
                row = await build_guided_row(values['tax_context'], text)
                token = f'tax_save_{token_hex(8)}'
                await state.update_data(tax_row=dict(row, tax_date=row['tax_date'].isoformat()), tax_confirm=token)
                await state.set_state(TaxStates.preview)
                await message.answer(preview_text(row), reply_markup=with_back([('✅ Записать', token)],
                                     target=values['tax_back']), parse_mode=None)
                return
            if current==TaxStates.import_csv.state:
                if not message.document or not (message.document.file_name or '').lower().endswith('.csv'):
                    raise ValueError('Пришлите файл .csv или напишите «отмена».')
                if (message.document.file_size or 0)>2_000_000:
                    raise ValueError('Файл больше 2 МБ.')
                destination=io.BytesIO()
                await message.bot.download(message.document,destination=destination)
                result=await accounting.import_entries(parse_import(destination.getvalue()),message.from_user.id)
                answer=f"Добавлено: {result['inserted']}; уже были учтены: {result['duplicates']}."
            elif current==TaxStates.entry.state:
                payload=import_template()+text.encode('utf-8')+b'\n'
                row = parse_import(payload)[0]
                token = f'tax_save_{token_hex(8)}'
                await state.set_data(dict(tax_back='tax_entry', tax_confirm=token,
                                          tax_row=dict(row, tax_date=row['tax_date'].isoformat())))
                await state.set_state(TaxStates.preview)
                await message.answer(preview_text(row), reply_markup=with_back([('✅ Записать', token)],
                                     target='tax_entry'), parse_mode=None)
                return
            elif current==TaxStates.contribution.state:
                fields=text.split(';')
                if len(fields)!=7:
                    raise ValueError('Нужно 7 полей через «;».')
                source_year,kind,due,used,allocated,effective,document=[f.strip() for f in fields]
                await accounting.allocate_contribution(source_year=int(source_year),kind=kind,tax_year=2026,
                     due_kop=money_kop(due),used_elsewhere_kop=money_kop(used),allocated_kop=money_kop(allocated),
                     effective_date=parse_date(effective),document=document,actor_id=message.from_user.id)
                answer='Распределение взноса сохранено. Уплата самого взноса этим не подтверждается.'
            elif current==TaxStates.bank.state:
                await accounting.update_profile(actor_id=message.from_user.id,bank_details=text)
                answer='Реквизиты сохранены.'
            else:
                await accounting.sync_sources()
                await accounting.update_profile(actor_id=message.from_user.id,reconciled_through=parse_date(text))
                answer='Сверка отмечена. Новые записи потребуют повторной сверки.'
            await state.clear()
            await message.answer(answer,reply_markup=back(),parse_mode=None)
        except ValueError as exc:
            await message.answer(str(exc),reply_markup=back(),parse_mode=None)
        except Exception:
            logging.exception('Tax input failed')
            await message.answer('Операция не завершена. Можно повторить ввод; повторный импорт не создаёт дублей.',reply_markup=back())

    # Legacy installs many catch-all message handlers. Accounting FSM must run
    # before them, without changing any existing booking handler's filter.
    handler=legacy.dp.message.handlers.pop()
    legacy.dp.message.handlers.insert(0,handler)
    legacy._tax_accounting_installed=True
