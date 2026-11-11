"""Read-only actual-Frappe report regressions. No user/fiscal fixtures or commits."""
import copy
from unittest.mock import patch
import uuid
import frappe
from frappe.tests.utils import FrappeTestCase
from angola_tax.reporting import position,declarations,payments,disputes
from angola_tax.reporting.access import Reader
from angola_tax.reporting.setup import REPORTS

MODULES=[position,declarations,payments,disputes]
SOURCE_TYPES=['Fiscal Obligation','Tax Declaration','Tax Declaration Type','Tax Deadline Configuration',
 'Tax Payment','Tax Collection Reference','Tax Payment Plan','Tax Assessment','Tax Notification','Tax Litigation',
 'Tax Litigation DAP','Tax Litigation Reclamacao Administrativa','Tax Litigation Recurso Hierarquico',
 'Tax Litigation Impugnacao Judicial','Tax Provision','Tax Alert Rule','Tax Warning','Notification Log','GL Entry']

class TestTaxReportingSite(FrappeTestCase):
    def setUp(self):
        frappe.set_user('Administrator')
        self.company=frappe.db.get_value('Company',{},'name')
        if not self.company:self.skipTest('A company is required on the reporting test site.')

    def test_all_four_registered_reports_load(self):
        for label,name,slug in REPORTS:
            report=frappe.get_doc('Report',name)
            self.assertEqual(report.report_type,'Script Report');self.assertEqual(report.is_standard,'Yes')
            self.assertFalse(report.add_total_row)
        for module in MODULES:
            result=module.execute({'company':self.company})
            self.assertEqual(len(result),6);self.assertTrue(result[5]);self.assertIsInstance(result[1],list)

    def test_denied_company_is_rejected_by_every_report(self):
        unknown='__reports_denied_'+uuid.uuid4().hex
        for module in MODULES:
            with self.assertRaises(frappe.PermissionError):module.execute({'company':unknown})

    def test_all_reports_leave_sources_and_notification_counts_unchanged(self):
        before={};counts={}
        for dt in SOURCE_TYPES:
            counts[dt]=frappe.db.count(dt)
            if dt not in ('Notification Log','GL Entry'):
                for name in frappe.get_all(dt,pluck='name',limit_page_length=0):before[(dt,name)]=copy.deepcopy(frappe.get_doc(dt,name).as_dict())
        with patch('frappe.sendmail',side_effect=AssertionError('A report must not send email')):
            for module in MODULES:module.execute({'company':self.company})
        for dt,count in counts.items():self.assertEqual(frappe.db.count(dt),count,dt)
        for (dt,name),facts in before.items():self.assertEqual(frappe.get_doc(dt,name).as_dict(),facts,(dt,name))

    def test_fiscal_position_reuses_stored_amounts_and_dates(self):
        from angola_tax.statutory_deadline import get_statutory_payment_due_date
        rows=position.execute({'company':self.company,'include_drafts':1,'include_closed':1})[1]
        for row in rows:
            source=frappe.get_doc('Fiscal Obligation',row['obligation'])
            self.assertEqual(row['confirmed_paid'],source.amount_paid)
            self.assertEqual(row['effective_amount'],source.effective_obligation_amount)
            self.assertEqual(row['payment_due'],get_statutory_payment_due_date(source))
            self.assertFalse(source.superseded_by)
            self.assertEqual(row['payable_outstanding'] if source.obligation_type=='Payable' else row['receivable_outstanding'],source.outstanding_amount)

    def test_payment_totals_follow_confirmed_references(self):
        rows=payments.execute({'company':self.company,'include_inactive':1})[1]
        for row in rows:
            reference=frappe.get_doc('Tax Collection Reference',row['collection_reference'])
            self.assertEqual(row['confirmed_amount'],reference.amount if reference.status=='Confirmed' else None)
            if row['tax_payment']:
                self.assertEqual(row['payment_status'],frappe.db.get_value('Tax Payment',row['tax_payment'],'status'))

    def test_declared_delivery_is_not_inferred_from_internal_approval(self):
        rows=declarations.execute({'company':self.company,'all_versions':1,'include_missing':1})[1]
        for row in rows:
            if row.get('declaration'):
                source=frappe.get_doc('Tax Declaration',row['declaration'])
                self.assertEqual(row['delivery_state'],source.delivery_state)
                self.assertEqual(row['preparation_state'],source.preparation_state)
            else:
                self.assertNotIn('delivery_state',row);self.assertTrue(row['configuration'])

    def test_inaccessible_related_payment_is_not_exposed(self):
        original=Reader.linked
        def guarded(reader,doctype,name):
            if doctype=='Tax Payment':return None
            return original(reader,doctype,name)
        with patch.object(Reader,'linked',guarded):
            rows=payments.execute({'company':self.company})[1]
        for row in rows:
            self.assertIsNone(row['tax_payment']);self.assertIsNone(row['payment_status'])
            self.assertIsNone(row['dc_pago_reference']);self.assertIsNone(row['evidence'])

    def test_invalid_date_interval_is_rejected(self):
        for module in MODULES:
            with self.assertRaises(frappe.ValidationError):
                module.execute({'company':self.company,'from_date':'2026-10-31','to_date':'2026-10-01'})
