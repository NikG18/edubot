"""Printable title/section I of the KUDiR and machine-readable audit exports."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak

from agent_report_pdf import _register_fonts
from tax_rules import calculate_tax, format_money


def safe_csv(value):
    # Strings can originate in counterparty/document names. Prevent spreadsheet formulas.
    text=str(value if value is not None else '')
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text


def register_csv(entries, *, audit=False):
    out=io.StringIO(newline='');writer=csv.writer(out,delimiter=';')
    if audit:
        writer.writerow(['operation_id','date','document','kind','gross_rub','income_rub',
                         'usn_payment_rub','tax_year','source_key','original_id','description'])
        for r in entries:
            writer.writerow([safe_csv(r['operation_id']),r['tax_date'].isoformat(),safe_csv(r['document']),r['kind'],
                             format_money(r['gross_kop']),format_money(r['income_kop']),
                             format_money(r['tax_paid_kop']),r['tax_year'],safe_csv(r.get('source_key')),r.get('original_id') or '',
                             safe_csv(r['description'])])
    else:
        writer.writerow(['№ п/п','Дата и номер первичного документа','Содержание операции',
                         'Доходы, учитываемые при исчислении налоговой базы, руб.',
                         'Расходы, учитываемые при исчислении налоговой базы, руб.'])
        n=0
        for r in entries:
            if not r['income_kop']:
                continue
            n+=1
            writer.writerow([n,safe_csv(f"{r['tax_date']:%d.%m.%Y}; {r['document']}"),safe_csv(r['description']),
                             format_money(r['income_kop']),''])
    return ('\ufeff'+out.getvalue()).encode('utf-8')


def build_kudir_pdf(profile, entries, *, year, complete=False, as_of=None):
    normal,bold=_register_fonts()
    body=ParagraphStyle('TaxBody',fontName=normal,fontSize=9,leading=12)
    small=ParagraphStyle('TaxSmall',parent=body,fontSize=8,leading=10)
    heading=ParagraphStyle('TaxHeading',parent=body,fontName=bold,fontSize=15,leading=19,spaceAfter=12)
    def p(value,style=body):
        return Paragraph(str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;'),style)
    out=io.BytesIO()
    doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=14*mm,rightMargin=14*mm,
                          topMargin=14*mm,bottomMargin=16*mm,title=f'КУДиР {year}',author=profile['taxpayer_name'])
    story=[p('КНИГА УЧЕТА ДОХОДОВ И РАСХОДОВ',heading),
           p('организаций и индивидуальных предпринимателей, применяющих упрощенную систему налогообложения'),
           Spacer(1,10*mm),p(f'На {year} год',heading),
           p(f"Налогоплательщик: {profile['taxpayer_name']}"),
           p(f"ИНН: {profile['taxpayer_inn']}"),
           p('Объект налогообложения: доходы'),
           p('Единица измерения: рубль'),
           p(f"Начало применения УСН: {profile['start_date']:%d.%m.%Y}"),
           p(f"Номера расчетных и иных счетов, наименования банков: {profile['bank_details'] or 'НЕ ЗАПОЛНЕНО'}"),
           Spacer(1,12*mm),
           p('Титульный лист и раздел I. Форма по приказу ФНС России от 07.11.2023 № ЕА-7-3/816@.',small),
           p('Разделы для объекта «доходы минус расходы» и торгового сбора не формируются: '
             'модуль предназначен для репетиторской деятельности ИП на УСН «доходы» без торгового сбора.',small),
           p('СВЕРЕННАЯ ВЫГРУЗКА' if complete else 'ПРЕДВАРИТЕЛЬНАЯ ВЫГРУЗКА: сверка не завершена.',heading),
           p(f"Сформировано: {(as_of or datetime.now()).strftime('%d.%m.%Y')}",small)]
    running=0;n=0
    for quarter in (1,2,3,4):
        story += [PageBreak(),p('I. Доходы и расходы',heading),p(f'{quarter} квартал {year} года')]
        rows=[[p(t,small) for t in ['№ п/п','Дата и номер первичного документа','Содержание операции',
                'Доходы, учитываемые при исчислении налоговой базы','Расходы, учитываемые при исчислении налоговой базы']],
              [p(i,small) for i in (1,2,3,4,5)]]
        quarter_sum=0
        for r in entries:
            if r['tax_date'].year!=year or (r['tax_date'].month-1)//3+1!=quarter or not r['income_kop']:
                continue
            n+=1;quarter_sum+=int(r['income_kop'])
            rows.append([p(n,small),p(f"{r['tax_date']:%d.%m.%Y}; {r['document']}",small),
                         p(r['description'],small),p(format_money(r['income_kop']),small),p('',small)])
        if len(rows)==2:
            rows.append([p('-',small),p('',small),p('Нет учтённых операций',small),p('0,00',small),p('',small)])
        running+=quarter_sum
        rows.append([p('',small),p('',small),p(f'Итого за {quarter} квартал',small),p(format_money(quarter_sum),small),p('',small)])
        if quarter>1:
            rows.append([p('',small),p('',small),p('Итого нарастающим итогом с начала года',small),p(format_money(running),small),p('',small)])
        table=Table(rows,colWidths=[10*mm,42*mm,62*mm,36*mm,32*mm],repeatRows=2)
        table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.4,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP'),
                                  ('BACKGROUND',(0,0),(-1,1),colors.HexColor('#eeeeee')),
                                  ('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),
                                  ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.append(table)
    def footer(canvas,doc):
        canvas.setFont(normal,8)
        canvas.drawRightString(A4[0]-14*mm,9*mm,f'Страница {doc.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()


async def export_bundle(year,actor_id):
    import tax_accounting as accounting
    await accounting.sync_sources()
    profile,entries,allocations,pending=await accounting.read_snapshot(year)
    count=len(pending)
    now=datetime.now(accounting.db.MSK)
    complete=accounting.is_reconciled(profile,min(now.date(),date(year,12,31)),pending)
    pdf=build_kudir_pdf(profile,entries,year=year,complete=complete,as_of=now)
    async with accounting.db._legacy.pool.acquire() as conn:
        await conn.execute('''INSERT INTO tax_exports(tax_year,pdf_bytes,pdf_sha256,entries_snapshot,created_by)
                              VALUES($1,$2,$3,$4::jsonb,$5)''',year,pdf,hashlib.sha256(pdf).hexdigest(),
                           json.dumps({'profile':profile,'entries':entries,'allocations':allocations},default=str,ensure_ascii=False),actor_id)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'KUDIR_{year}.pdf',pdf)
        z.writestr(f'KUDIR_section_I_{year}.csv',register_csv(entries))
        z.writestr(f'operations_audit_{year}.csv',register_csv(entries,audit=True))
        z.writestr('unreconciled.json',json.dumps(pending,default=str,ensure_ascii=False,indent=2))
        calculations=[calculate_tax(entries,allocations,year=year,quarter=q,rate=Decimal(profile['rate']),
                        start=profile['start_date'],as_of=now.date(),has_workers=profile['has_workers']) for q in (1,2,3,4)]
        z.writestr('tax_calculation.json',json.dumps(calculations,default=str,ensure_ascii=False,indent=2))
        z.writestr('README.txt','КУДиР: титульный лист и раздел I.\n'
                  f'Сверка завершена: {complete}. Неразобранных источников: {count}.\n'
                  'Расчёт УСН не равен сальдо ЕНС. Отправка декларации/уведомлений и уплата не производятся.\n'
                  'Для бумажного хранения книгу распечатывают, нумеруют, прошнуровывают и заверяют подписью ИП.\n')
    return buf.getvalue(),complete,count
