"""Run in the test site after migrate/setup. No tax source is recalculated or posted."""
from uuid import uuid4
from unittest.mock import patch
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime
from angola_tax.declarations.controller import store_file

class TestDeclarationLifecycle(FrappeTestCase):
    def setUp(self):
        frappe.set_user('Administrator')
        companies=frappe.get_all('Company',pluck='name',limit_page_length=1)
        if not companies:self.skipTest('Use an ERPNext test site with an existing Company.')
        self.company=companies[0]
        code='DECL-TEST-'+uuid4().hex[:12]
        self.typ=frappe.get_doc({'doctype':'Tax Declaration Type','filing_code':code,
            'statutory_name':'Lifecycle fixture','purpose':'Information','adapter_key':'II Annual Portal v1',
            'schema_version':'Test fixture','verification_state':'Verified','legal_sources':'Test fixture only',
            'completion_rule':'Acknowledged','supports_replacement':1}).insert()
        self.config=frappe.get_doc({'doctype':'Tax Deadline Configuration','company':self.company,
            'declaration_purpose':'Declaration','declaration_type':self.typ.name,
            'declaration_applicability':'Required','declaration_reason':'Isolated integration fixture',
            'effective_from':'2025-01-01','active':1,'deadline_rules':[{
                'deadline_type':'Submission','periodicity':'Monthly','due_month_offset':1,
                'use_end_of_month':1,'declaration_anchor':'Period End'}]}).insert()
        self.config.submit()
        self.doc=frappe.get_doc({'doctype':'Tax Declaration','company':self.company,
            'declaration_type':self.typ.name,'deadline_configuration':self.config.name,
            'period_start':'2025-01-01','period_end':'2025-01-31','responsible_user':'Administrator',
            'submission_kind':'Original','revision':1}).insert()
        self.provider_value={'manifest':{'company':{'tax_id':'TEST-NIF'},'source':{'fixed_amount':0}},
            'manifest_hash':'test-hash','values':{'tax_amount':0},'source_obligation':None,'extension':'json'}
        self.provider=patch('angola_tax.declarations.providers.collect',return_value=self.provider_value)
        self.provider.start();self.addCleanup(self.provider.stop)

    def approve(self,doc=None):
        doc=doc or self.doc;doc.prepare();doc.reload();doc.submit();doc.reload();return doc

    def evidence(self,doc,label='receipt'):
        return store_file(doc,label+'-'+uuid4().hex+'.txt',('External portal evidence '+uuid4().hex).encode())

    def test_nil_information_approval_never_submits_or_posts(self):
        before={d:frappe.db.count(d,{'company':self.company})for d in ('GL Entry','Fiscal Obligation','Tax Settlement','Tax Payment')}
        doc=self.approve()
        self.assertEqual(doc.delivery_state,'Not Submitted');self.assertEqual(doc.obligations,[])
        for kind,total in before.items():self.assertEqual(frappe.db.count(kind,{'company':self.company}),total)

    def test_generated_file_is_not_portal_evidence(self):
        doc=self.approve()
        with self.assertRaises(frappe.ValidationError):
            doc.record_delivery('Submission',str(now_datetime()),doc.generated_file,'PORTAL-1','TRY-1')

    def test_evidence_retries_and_pending_replacement(self):
        doc=self.approve();time=str(now_datetime());receipt=self.evidence(doc)
        doc.record_delivery('Submission',time,receipt,'PORTAL-1','TRY-1')
        doc.reload();count=len(doc.events)
        doc.record_delivery('Submission',time,receipt,'PORTAL-1','TRY-1')
        doc.reload();self.assertEqual(len(doc.events),count)
        doc.record_delivery('Accepted',str(now_datetime()),self.evidence(doc,'acceptance'),'ACCEPT-1','TRY-1')
        revision_name=doc.create_revision('Replacement','Correct reporting metadata')
        revision=frappe.get_doc('Tax Declaration',revision_name)
        original=frappe.get_doc('Tax Declaration',doc.name)
        self.assertEqual(original.effective_accepted_version,original.name)
        self.assertEqual(original.chain_state,'Replacement Pending')
        self.assertEqual(revision.delivery_state,'Not Submitted')
        self.assertEqual(revision.revision,2)

    def test_direct_edit_and_delete_of_approved_evidence_blocked(self):
        doc=self.approve();doc.events[0].reason='Rewritten history'
        with self.assertRaises(frappe.ValidationError):doc.save()
        doc.reload();doc.source_name='silently changed source'
        with self.assertRaises(frappe.ValidationError):doc.save()

    def test_source_changes_do_not_overwrite_snapshot(self):
        doc=self.approve();frozen=doc.snapshot_file
        changed=dict(self.provider_value);changed['values']={'tax_amount':25}
        with patch('angola_tax.declarations.providers.collect',return_value=changed):
            self.assertEqual(doc.check_sources(),'Sources Changed')
        doc.reload();self.assertEqual(doc.snapshot_file,frozen)

    def test_duplicate_original_blocked(self):
        duplicate=frappe.get_doc({'doctype':'Tax Declaration','company':self.company,
            'declaration_type':self.typ.name,'deadline_configuration':self.config.name,
            'period_start':'2025-01-01','period_end':'2025-01-31','responsible_user':'Administrator',
            'submission_kind':'Original','revision':1})
        with self.assertRaises(frappe.DuplicateEntryError):duplicate.insert()
