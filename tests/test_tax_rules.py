import csv
import io
import unittest
from datetime import date
from decimal import Decimal

from tax_rules import (calculate_tax, money_kop, parse_import, import_template,
                       validate_allocation, additional_contribution)
from tax_export import register_csv, build_kudir_pdf


def entry(amount, when=date(2026,8,10), **kwargs):
    return dict(tax_date=when,income_kop=amount,tax_paid_kop=0,tax_year=2026,**kwargs)


def calc(entries, allocations=(), quarter=3, as_of=date(2026,12,31)):
    return calculate_tax(entries,list(allocations),year=2026,quarter=quarter,rate=Decimal(6),
                         start=date(2026,8,7),as_of=as_of)


class TaxRulesTests(unittest.TestCase):
    def test_cumulative_income_refunds_transit_and_tax_paid(self):
        rows=[entry(10_000_000),entry(0),entry(-1_000_000,date(2026,9,1)),
              entry(5_000_000,date(2026,10,1)),entry(99_999_999,date(2026,8,6))]
        rows.append(dict(tax_date=date(2026,10,28),income_kop=0,tax_paid_kop=540_000,tax_year=2026))
        self.assertEqual(calc(rows)['income_kop'],9_000_000)
        self.assertEqual(calc(rows)['net_tax_rub'],5400)
        annual=calc(rows,quarter=4)
        self.assertEqual(annual['net_tax_rub'],8400)
        self.assertEqual(annual['advance_due_rub'],3000)
        self.assertEqual(annual['balance_kop'],300_000)

    def test_deductions_in_separate_periods_and_no_fifty_percent_cap(self):
        allocations=[dict(tax_year=2026,effective_date=date(2026,8,7),allocated_kop=200_000),
                     dict(tax_year=2026,effective_date=date(2026,10,1),allocated_kop=500_000)]
        self.assertEqual(calc([entry(10_000_000)],allocations)['net_tax_rub'],4000)
        annual=calc([entry(10_000_000)],allocations,quarter=4)
        self.assertEqual(annual['net_tax_rub'],0)
        self.assertEqual(annual['advance_reduction_rub'],4000)

    def test_round_once_after_kopeck_deductions(self):
        allocations=[dict(tax_year=2026,effective_date=date(2026,8,7),allocated_kop=2)]
        result=calc([entry(2501)],allocations)
        self.assertEqual(result['gross_tax_rub'],2)
        self.assertEqual(result['net_tax_rub'],1)

    def test_next_year_payment_reduces_prior_year_balance_only(self):
        rows=[entry(10_000_000),dict(tax_date=date(2027,4,1),income_kop=0,tax_paid_kop=600_000,tax_year=2026)]
        self.assertEqual(calc(rows,quarter=4)['balance_kop'],600_000)
        self.assertEqual(calc(rows,quarter=4,as_of=date(2027,4,2))['balance_kop'],0)

    def test_current_period_is_partial(self):
        result=calc([entry(10000),entry(10000,date(2026,9,30))],as_of=date(2026,9,25))
        self.assertFalse(result['closed'])
        self.assertEqual(result['income_kop'],10000)

    def test_extra_contribution_threshold_cap_and_no_automatic_deduction(self):
        self.assertEqual(additional_contribution(30_000_000,2026),0)
        self.assertEqual(additional_contribution(40_000_000,2026),100_000)
        self.assertEqual(additional_contribution(10_000_000_000,2026),32_181_800)
        self.assertEqual(calc([entry(40_000_000)])['eligible_deduction_kop'],0)

    def test_money_rejects_nonfinite_negative_and_fractions(self):
        for value in ['NaN','Infinity','-1','1.001','not money']:
            with self.subTest(value=value),self.assertRaises(ValueError): money_kop(value)
        self.assertEqual(money_kop('1 234,56'),123456)

    def test_import_header_bom_year_and_strict_fields(self):
        row='p1;01.04.2027;ЕНС;tax_payment;123,45;;;Погашение;2026\n'
        parsed=parse_import(import_template()+row.encode())
        self.assertEqual(parsed[0]['tax_year'],2026)
        self.assertEqual(parsed[0]['amount_kop'],12345)
        with self.assertRaises(ValueError): parse_import(import_template()+(row+row).encode())
        with self.assertRaises(ValueError): parse_import(import_template()+b'p1;bad\n')

    def test_contribution_allocation_prevents_double_use(self):
        args=dict(kind='extra',source_year=2025,tax_year=2026,due_kop=10000,
                  allocated_kop=5000,other_allocated_kop=5000,effective_date=date(2026,8,7))
        validate_allocation(**args)
        with self.assertRaises(ValueError): validate_allocation(**(args|{'allocated_kop':5001}))
        with self.assertRaises(ValueError): validate_allocation(**(args|{'kind':'fixed'}))

    def test_exports_escape_strings_and_omit_transit_from_kudir(self):
        row=entry(250000,operation_id='=1',document='=HYPERLINK()',kind='income',gross_kop=250000,
                  description='<Оплата> & тест',source_key=None,original_id=None)
        transit=row|dict(operation_id='transit',kind='transit',income_kop=0)
        data=list(csv.reader(io.StringIO(register_csv([row,transit]).decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(data),2)
        audit=register_csv([row],audit=True).decode('utf-8-sig')
        self.assertIn("'=HYPERLINK()",audit)
        profile=dict(taxpayer_name='ИП Тест',taxpayer_inn='123456789012',start_date=date(2026,8,7),bank_details='Тестовый банк')
        self.assertTrue(build_kudir_pdf(profile,[row,transit],year=2026).startswith(b'%PDF-'))
