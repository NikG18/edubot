"""Telegram-only accounting admin UI. No real bank or FNS actions are performed."""
from __future__ import annotations
import io
import logging
from datetime import datetime

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import tax_accounting as accounting
from tax_rules import (format_money, import_template, parse_date, parse_import, money_kop)
from tax_export import export_bundle


class TaxStates(StatesGroup):
    entry=State()
    import_csv=State()
    contribution=State()
    bank=State()
    reconciliation=State()


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
          ('📄 Выгрузить КУДиР','tax_export'),('🧾 Взносы и уменьшение','tax_contributions'),
          ('🏦 Реквизиты счёта','tax_bank'),('✅ Завершить сверку','tax_reconcile'),
          ('ℹ️ Правила и шаблон','tax_help'),('🔙 В админ-панель','admin_panel_open')]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t,callback_data=c)] for t,c in rows])


def back():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔙 Бухгалтерия',callback_data='admin_tax')]])


def install_telegram_tax_accounting(app):
    legacy=app.legacy
    if getattr(legacy,'_tax_accounting_installed',False):
        return
    original=legacy.admin_actions_keyboard
    def admin_keyboard():
        kb=original()
        kb.inline_keyboard.insert(max(0,len(kb.inline_keyboard)-1),[
            InlineKeyboardButton(text='📒 Бухгалтерия / КУДиР',callback_data='admin_tax')])
        return kb
    legacy.admin_actions_keyboard=admin_keyboard

    async def allowed(source):
        if source.from_user.id==legacy.ADMING_ID:
            return True
        if hasattr(source,'data'):
            await legacy.safe_answer(source,'⛔ Только администратор',show_alert=True)
        else:
            await source.answer('⛔ Только администратор')
        return False

    @legacy.dp.callback_query(F.data=='admin_tax')
    async def menu(call,state):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        await state.clear()
        try:
            await accounting.sync_sources()
            profile=await accounting.get_profile()
            count,_=await accounting.unresolved(limit=1)
            text=(f"📒 УСН «Доходы» {profile['rate']:g}%\n"
                  f"Начало учёта: {profile['start_date']:%d.%m.%Y}\n"
                  f"Операций на сверке: {count}\n"
                  f"Взносы: {'настроены' if profile['contributions_reviewed'] else 'нужно указать уменьшение'}\n\n"
                  'Выберите действие. Суммы налога рассчитываются по подтверждённым операциям.')
            await call.message.edit_text(text,reply_markup=keyboard(),parse_mode=None)
        except Exception:
            logging.exception('Tax menu failed')
            await call.message.answer('Не удалось открыть учёт. Данные не изменены.',reply_markup=back())

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

    @legacy.dp.callback_query(F.data.regexp(r'^tax_pending_\d+$'))
    async def pending(call):
        if not await allowed(call): return
        await legacy.safe_answer(call)
        await accounting.sync_sources()
        offset=int(call.data.rsplit('_',1)[1]);count,rows=await accounting.unresolved(limit=8,offset=offset)
        lines=[f'🔎 На сверке: {count}. Ключи нужны для поля source_key.\n']
        for r in rows:
            lines.append(f"{r['source_key']} | {format_money(r['gross_kop'])} ₽ | {r['role']}\n{r['description']}\n")
        kb=back().inline_keyboard
        if offset+8<count:
            kb.insert(0,[InlineKeyboardButton(text='Следующие',callback_data=f'tax_pending_{offset+8}')])
        await call.message.edit_text('\n'.join(lines),reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),parse_mode=None)

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
        await state.clear();await state.set_state(target)
        kb=back()
        if call.data=='tax_contributions':
            kb.inline_keyboard.insert(0,[InlineKeyboardButton(text='Пока считать без уменьшения',callback_data='tax_no_deductions')])
        await call.message.edit_text(text,reply_markup=kb,parse_mode=None)

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

    @legacy.dp.message(StateFilter(TaxStates.entry,TaxStates.import_csv,TaxStates.contribution,
                                   TaxStates.bank,TaxStates.reconciliation))
    async def save(message,state):
        if not await allowed(message): return
        current=await state.get_state()
        text=(message.text or '').strip()
        if text.lower() in {'отмена','назад','/cancel'}:
            await state.clear();await message.answer('Ввод отменён.',reply_markup=back());return
        try:
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
                result=await accounting.import_entries(parse_import(payload),message.from_user.id)
                answer=f"Добавлено: {result['inserted']}; повторов: {result['duplicates']}."
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
