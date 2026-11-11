"""Actual Frappe tests. Run on a test site, never as installation side effects.
Warning fixtures use an existing readable obligation; fiscal source documents are untouched.
"""
import copy
import json
import uuid
from datetime import timedelta
from unittest.mock import patch
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from angola_tax.alerts import adapters,service
from angola_tax.alerts.permissions import source_allowed,as_user

def rule_fixture(company,responsible_user,escalation_user):
    """Reuse installed disabled policy in memory; never duplicate its unique scope."""
    settings={'doctype':'Tax Alert Rule','rule_title':'Regression fixture','company':company,
        'condition':'payment_due','trigger_label':'Payment deadline','fiscal_process':'Payments','enabled':0,
        'kind':'Alert','severity':'High','overdue_severity':'Critical','responsible_user':responsible_user,
        'escalation_user':escalation_user,'advance_days':7,'recurrence_hours':24,'escalation_hours':48,'notify_in_app':1}
    existing=frappe.db.get_value('Tax Alert Rule',{'company':company,'condition':'payment_due'},'name')
    if existing:
        doc=frappe.get_doc('Tax Alert Rule',existing)
        if doc.enabled:raise AssertionError('Run this suite on a test site with the payment-deadline rule disabled.')
        if frappe.db.exists('Tax Warning',{'rule':existing}):raise AssertionError('Use an isolated test site: this rule already has live warning history.')
        doc.update(settings)
        return doc  # Do not save changes to the existing company's rule.
    return frappe.get_doc(settings).insert(ignore_permissions=True)

class TestTaxAlertsSite(FrappeTestCase):
    def setUp(self):
        frappe.set_user('Administrator')
        # unittest runs cleanups even if setUp fails. Never leave fixture writes behind.
        self.addCleanup(frappe.db.rollback)
        self.company=frappe.db.get_value('Fiscal Obligation',{},'company')
        if not self.company:self.skipTest('Create a fiscal obligation fixture on the test site first.')
        self.source=frappe.get_doc('Fiscal Obligation',frappe.db.get_value('Fiscal Obligation',{'company':self.company},'name'))
        self.before=copy.deepcopy(self.source.as_dict())
        self.uid='alerts-test-'+uuid.uuid4().hex[:12]+'@example.invalid'
        self.boss='alerts-boss-'+uuid.uuid4().hex[:12]+'@example.invalid'
        for uid in (self.uid,self.boss):
            frappe.get_doc({'doctype':'User','email':uid,'first_name':'Tax Alerts Test','enabled':1,
                'send_welcome_email':0,'user_type':'System User','roles':[{'role':'System Manager'}]}).insert(ignore_permissions=True)
            frappe.get_doc({'doctype':'User Permission','user':uid,'allow':'Company','for_value':self.company,'apply_to_all_doctypes':1}).insert(ignore_permissions=True)
        self.rule=rule_fixture(self.company,self.uid,self.boss)
        self.data=adapters.candidate(self.rule,self.source,'Regression warning fixture','Review fixture',detail=uuid.uuid4().hex)
        self.warning_names=[]

    def tearDown(self):
        frappe.set_user('Administrator')
        # These tests neither call the global scheduler nor commit their fixtures.
        self.assertEqual(self.before,frappe.get_doc('Fiscal Obligation',self.source.name).as_dict())
        frappe.db.rollback()
        super().tearDown()

    def warning(self):
        service.reconcile_rule(self.rule,[self.data])
        return frappe.get_doc('Tax Warning',{'condition_key':self.data['condition_key']})

    def test_unique_condition_and_history(self):
        doc=self.warning();self.warning();self.warning()
        self.assertEqual(frappe.db.count('Tax Warning',{'condition_key':self.data['condition_key']}),1)
        doc.reload();self.assertEqual([r.action for r in doc.history],['Opened'])

    def test_acknowledge_resolve_reopen(self):
        doc=self.warning()
        service.act(doc.name,'Acknowledge');doc.reload();self.assertEqual(doc.status,'Acknowledged')
        self.warning().reload();doc.reload();self.assertEqual(doc.status,'Acknowledged')
        service.reconcile_rule(self.rule,[]);doc.reload();self.assertEqual(doc.status,'Resolved')
        self.warning();doc.reload();self.assertEqual(doc.status,'Open');self.assertEqual(doc.episode,2)
        self.assertEqual([r.action for r in doc.history],['Opened','Acknowledge','Resolved','Reopened'])

    def test_snooze_dismiss_and_no_manual_resolution(self):
        doc=self.warning()
        with self.assertRaises(Exception):service.act(doc.name,'Snooze',until=now_datetime()+timedelta(days=1))
        service.act(doc.name,'Snooze',reason='Awaiting source review',until=now_datetime()+timedelta(days=1))
        doc.reload();self.assertEqual(doc.status,'Snoozed')
        service.act(doc.name,'Dismiss',reason='Later review');doc.reload();self.assertEqual(doc.status,'Dismissed')
        with self.assertRaises(Exception):service.act(doc.name,'Resolve')
        service.reconcile_rule(self.rule,[]);doc.reload();self.assertEqual(doc.status,'Resolved')

    def test_duplicate_notifications_and_no_email(self):
        doc=self.warning()
        with patch('frappe.sendmail',side_effect=AssertionError('Email is forbidden')):
            service.dispatch(doc,self.rule);service.dispatch(doc,self.rule)
        logs=frappe.get_all('Tax Warning Delivery',filters={'warning':doc.name},fields=['recipient','notification_log'])
        self.assertEqual(len(logs),1);self.assertEqual(logs[0].recipient,self.uid)
        self.assertTrue(frappe.db.exists('Notification Log',logs[0].notification_log))

    def test_escalation_history_and_no_backlog(self):
        doc=self.warning();doc.first_seen=now_datetime()-timedelta(days=10)
        with service.internal_write():doc.save(ignore_permissions=True)
        with patch('frappe.sendmail',side_effect=AssertionError('Email is forbidden')):
            service.dispatch(doc,self.rule);service.dispatch(doc,self.rule)
        logs=frappe.get_all('Tax Warning Delivery',filters={'warning':doc.name},fields=['recipient','stage'])
        self.assertEqual(len(logs),2)
        self.assertEqual({r.recipient for r in logs},{self.uid,self.boss})
        doc.reload();self.assertIn('Escalated',[h.action for h in doc.history])

    def test_access_revocation_hides_warning_report_and_notification(self):
        doc=self.warning();service.dispatch(doc,self.rule)
        user=frappe.get_doc('User',self.uid);user.roles=[];user.append('roles',{'role':'Tax Approver'});user.append('roles',{'role':'Employee'});user.save(ignore_permissions=True)
        frappe.clear_cache(user=self.uid)
        # Existing fiscal obligation is System-Manager-only: remove that role to revoke its source access.
        self.assertFalse(source_allowed('Fiscal Obligation',self.source.name,self.company,self.uid))
        with as_user(self.uid):
            self.assertFalse(frappe.get_list('Tax Warning',filters={'name':doc.name}))
            self.assertFalse(frappe.get_list('Notification Log',filters={'document_type':'Tax Warning','document_name':doc.name}))
            from angola_tax.alerts.reports import execute
            from angola_tax.alerts.permissions import company_allowed
            if company_allowed(self.company):
                _,rows=execute({'company':self.company})
                self.assertFalse(any(r.name==doc.name for r in rows))
            else:
                # Report rejection is the correct outcome when company access is denied.
                with self.assertRaises(frappe.PermissionError):
                    execute({'company':self.company})

    def test_source_change_and_existing_integrations_are_read_only(self):
        # Run every suggested condition on real existing source documents, with in-app sending disabled.
        from angola_tax.alerts.catalog import CATALOG
        failures=[]
        source_types={v[1] for v in CATALOG.values()}|{'Industrial Tax Computation','Tax Settlement'}
        before={}
        for dt in source_types:
            for n in frappe.get_all(dt,pluck='name',limit_page_length=0):before[(dt,n)]=frappe.get_doc(dt,n).as_dict()
        for key,(process,dt,title) in CATALOG.items():
            rule=frappe._dict(self.rule.as_dict());rule.condition=key;rule.fiscal_process=process;rule.notify_in_app=0
            try:adapters.evaluate(rule)
            except Exception as exc:failures.append((key,type(exc).__name__))
        self.assertEqual(failures,[])
        for (dt,n),facts in before.items():self.assertEqual(facts,frappe.get_doc(dt,n).as_dict())

    def test_preview_does_not_persist_or_notify(self):
        baseline=(frappe.db.count('Tax Warning'),frappe.db.count('Notification Log'),frappe.db.count('Tax Declaration'))
        result=service.preview(rule=self.rule.name)
        self.assertEqual(result['documents_changed'],0);self.assertEqual(result['notifications_sent'],0)
        self.assertEqual(baseline,(frappe.db.count('Tax Warning'),frappe.db.count('Notification Log'),frappe.db.count('Tax Declaration')))
