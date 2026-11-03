"""Synthetic acceptance-runner tests; no archive, production model or key access."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history_evaluate as e


def key(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cases():
    return [dict(id='case-%02d' % i, category=('exact', 'paraphrase', 'correction', 'unsupported', 'project', 'attachment')[i % 6],
                 question='PRIVATE_QUESTION_%02d' % i, answerable=i % 6 != 3,
                 expectedSourceIds=[key(str(i))] if i % 6 != 3 else [], referenceAnswer='PRIVATE_REFERENCE') for i in range(30)]


class Store:
    def __init__(self, misses=()):
        self.misses = set(misses)
        self.records = {key(str(i)): dict(id=key(str(i)), revision=key('revision' + str(i)), kind='document' if i % 2 else 'conversation',
            title='PRIVATE_TITLE', text='PRIVATE_CORPUS_TEXT', timestamp=None, project='', metadata={'conversationId': 'shared'}) for i in range(30)}
    def search(self, question, project='', limit=5):
        index = int(question.rsplit('_', 1)[1])
        if index in self.misses:
            return []
        return [dict(id=key(str(index)), sourceId=key(str(index)), excerpt='PRIVATE_EXCERPT')]
    def status(self):
        return dict(generation={'generationId': 'a' * 32}, policyRevision=key('policy'))
    def source(self, source_id):
        return self.records.get(source_id)
    @contextmanager
    def _index(self):
        records = self.records
        class Database:
            def execute(_, sql):
                return [dict(id=row['id'], kind=row['kind'], metadata=row['metadata'], timestamp=row['timestamp'], textLength=len(row['text'])) for row in records.values()]
        yield Database()


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-history-evaluate-')
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()

    def test_minimum_and_expected_source_contract(self):
        with self.assertRaises(ValueError):
            e.fixture_cases(cases()[:29])
        data = cases(); data[0]['expectedSourceIds'] = []
        with self.assertRaises(ValueError):
            e.fixture_cases(data)
        data = cases(); data[3]['expectedSourceIds'] = [key('unanswerable')]
        with self.assertRaises(ValueError):
            e.fixture_cases(data)
        data = cases(); data[1]['id'] = data[0]['id']
        with self.assertRaises(ValueError):
            e.fixture_cases(data)

    def test_fixture_review_requires_matching_exact_file_hash(self):
        path = self.root / 'fixture.json'
        # A model-controlled reviewed flag cannot satisfy the explicit review gate.
        raw = e.encoded(dict(format=e.FIXTURE, reviewed=True, cases=cases()))
        path.write_bytes(raw)
        parsed, digest, reviewed = e.load_fixture(path)
        self.assertFalse(reviewed)
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
        with self.assertRaises(ValueError):
            e.load_fixture(path, '0' * 64)
        self.assertTrue(e.load_fixture(path, digest)[2])
        path.write_bytes(raw + b'\n')
        with self.assertRaises(ValueError):
            e.load_fixture(path, digest)

    def test_answerable_top5_gate_excludes_unsupported_from_denominator(self):
        report = e.evaluate(Store(misses={3, 9, 15, 21, 27}), cases(), key('fixture'), reviewed=True)
        summary = report['summary']
        self.assertEqual(summary['answerableCases'], 25)
        self.assertEqual(summary['unsupportedCases'], 5)
        self.assertEqual(summary['answerableHitRateAt5'], 1)
        self.assertTrue(summary['retrievalGatePassed'])
        self.assertEqual(summary['status'], 'retrieval_passed')
        self.assertEqual(summary['correctionCases'], 5)
        self.assertNotIn('PRIVATE_', json.dumps(summary))
        self.assertIn('PRIVATE_QUESTION', json.dumps(report))

    def test_ninety_percent_threshold_and_unreviewed_gate(self):
        passing = e.evaluate(Store(misses={0, 1}), cases(), key('fixture'), reviewed=True)['summary']
        failing = e.evaluate(Store(misses={0, 1, 2}), cases(), key('fixture'), reviewed=True)['summary']
        self.assertAlmostEqual(passing['answerableHitRateAt5'], .92)
        self.assertTrue(passing['retrievalGatePassed'])
        self.assertAlmostEqual(failing['answerableHitRateAt5'], .88)
        self.assertFalse(failing['retrievalGatePassed'])
        unreviewed = e.evaluate(Store(), cases(), key('fixture'))['summary']
        self.assertEqual(unreviewed['status'], 'pending_human_fixture_review')
        self.assertFalse(unreviewed['retrievalGatePassed'])

    def test_labels_alone_cannot_fake_required_unsupported_and_correction_strata(self):
        data = cases()
        for row in data:
            if not row['answerable']:
                row.update(answerable=True, expectedSourceIds=[key(row['question'].rsplit('_', 1)[1].lstrip('0') or '0')])
        report = e.evaluate(Store(), data, key('fixture'), reviewed=True)
        self.assertEqual(report['summary']['unsupportedCases'], 0)
        self.assertEqual(report['summary']['status'], 'missing_required_strata')
        self.assertFalse(report['summary']['retrievalGatePassed'])

    def test_answers_are_saved_for_review_without_automatic_truth_claim(self):
        def answer(question):
            return dict(content='Unknown; no evidence. PRIVATE_ANSWER', model='local/fixture', sourceIds=[])
        report = e.evaluate(Store(), cases(), key('fixture'), reviewed=True, answerer=answer)
        self.assertEqual(report['summary']['status'], 'answer_review_pending')
        self.assertFalse(report['summary']['answersReviewed'])
        self.assertTrue(report['cases'][3]['abstentionSignalOnly'])
        self.assertIn('PRIVATE_ANSWER', json.dumps(report))
        self.assertNotIn('PRIVATE_', json.dumps(report['summary']))

    def test_errors_do_not_copy_exception_text_or_secrets_to_summary(self):
        class Broken(Store):
            def search(self, *args, **kwargs):
                raise RuntimeError('PRIVATE_QUESTION PRIVATE_KEY')
        def broken_answer(_):
            raise ValueError('PRIVATE_KEY')
        report = e.evaluate(Broken(), cases(), key('fixture'), reviewed=True, answerer=broken_answer)
        self.assertEqual(report['summary']['retrievalErrors'], 30)
        self.assertEqual(report['summary']['answerErrors'], 30)
        self.assertNotIn('PRIVATE_KEY', json.dumps(report))
        self.assertFalse(report['summary']['retrievalGatePassed'])

    def test_private_owned_report_keeps_text_off_cli_output(self):
        root = e.report_root(self.root / 'private-report')
        e.private_write(root, 'evaluation.json', {'text': 'PRIVATE_REPORT'})
        self.assertEqual(json.loads((root / 'evaluation.json').read_text())['text'], 'PRIVATE_REPORT')
        self.assertEqual(e.report_root(root), root)
        with self.assertRaises(ValueError):
            e.private_write(root, '../escape.json', {})
        occupied = self.root / 'occupied'; occupied.mkdir(); (occupied / 'keep.txt').write_text('keep')
        with self.assertRaises(ValueError):
            e.report_root(occupied)

    def test_draft_builder_is_local_candidate_only_and_stratified(self):
        prompts = []
        def ask(prompt):
            prompts.append(prompt)
            match = re.search(r'artifact: ([a-f0-9]{32})', prompt)
            question = 'What is the serial for ' + match[1] + '?' if match else 'What does this source establish?'
            return {'content': json.dumps(dict(question=question, referenceAnswer='PRIVATE_DRAFT_REFERENCE'))}
        draft = e.draft_fixture(Store(), ask, 'local/fixture')
        self.assertFalse(draft['reviewed'])
        self.assertEqual(len(draft['cases']), 30)
        self.assertEqual({row['category'] for row in draft['cases']}, e.CATEGORIES)
        self.assertEqual(len(prompts), 30)
        self.assertEqual(sum(not row['answerable'] for row in draft['cases']), 5)
        self.assertEqual(len(e.fixture_cases(draft)), 30)

    def type_balanced_store(self):
        store = Store()
        store.records = {}
        def add(name, kind, meta, text='PRIVATE_FORMAT_FIXTURE', timestamp=None):
            value = dict(id=key(name), revision=key(text), kind=kind, title='PRIVATE_TITLE', text=text,
                         timestamp=timestamp, project='', metadata=meta)
            store.records[value['id']] = value
        add('conversation-early', 'conversation', {'conversationId': 'one', 'onCurrentBranch': True, 'role': 'user'}, timestamp='2026-11-01T00:00:00Z')
        add('conversation-later', 'conversation', {'conversationId': 'one', 'onCurrentBranch': True, 'role': 'user'}, timestamp='2026-11-02T00:00:00Z')
        add('group', 'group', {'conversationId': 'group'})
        add('page', 'page', {'pageId': 'page'})
        add('site', 'site', {'path': 'site/export.bundle', 'gitPath': 'index.js'})
        formats = {'pdf': {'page': 1}, 'docx': {'part': 'word/document.xml'}, 'xlsx': {'part': 'xl/worksheets/sheet1.xml', 'sheet': 'Fixture'},
                   'pptx': {'part': 'ppt/slides/slide1.xml', 'slide': 1}, 'text': {'originalName': 'note.txt'},
                   'json': {'originalName': 'data.json'}, 'csv': {'originalName': 'table.csv'},
                   'markdown': {'member': 'README.md'}, 'html': {'originalName': 'index.html'}}
        for kind, meta in formats.items():
            add(kind, 'document', dict(meta, assetId=kind, path='opaque-' + kind + '.dat'))
        # Thousands of records from ONE workbook must remain one source group.
        for i in range(5000):
            add('xlsx-row-' + str(i), 'document', {'assetId': 'xlsx', 'part': 'xl/worksheets/sheet1.xml', 'sheet': 'Fixture', 'row': str(i)},
                text='PRIVATE_XLSX_CELL ' + str(i))
        return store

    def test_sampler_covers_present_formats_without_xlsx_row_dominance(self):
        store = self.type_balanced_store()
        pools, coverage = e.sample_sources(store)
        self.assertEqual(set(pools), set(e.SOURCE_TYPES))
        self.assertEqual(coverage['recordsByType']['xlsx'], 5001)
        self.assertEqual(len(pools['xlsx']), 1)
        self.assertEqual(len(pools['conversation']), 2)
        self.assertLessEqual(sum(len(pool) for pool in pools.values()), len(e.SOURCE_TYPES) * 4 + 4)
        self.assertEqual(e.source_type({'kind': 'document', 'metadata': {'part': 'word/document.xml', 'originalName': 'wrong.xlsx'}}), 'docx')
        self.assertEqual(e.source_type({'kind': 'document', 'metadata': {'part': 'text', 'path': 'opaque.dat'}}), 'text')

    def test_sampler_prefers_selected_branch_with_explicit_alternate_fallback(self):
        store = Store()
        store.records = {key('alternate'): dict(id=key('alternate'), kind='conversation', title='PRIVATE', text='PRIVATE', timestamp='2026-11-02T00:00:00Z',
                        metadata={'conversationId': 'same', 'onCurrentBranch': False})}
        pools, _ = e.sample_sources(store)
        self.assertEqual(pools['conversation'][0]['id'], key('alternate'))
        store.records[key('selected')] = dict(id=key('selected'), kind='conversation', title='PRIVATE', text='PRIVATE', timestamp='2026-11-01T00:00:00Z',
                        metadata={'conversationId': 'same', 'onCurrentBranch': True})
        pools, _ = e.sample_sources(store)
        self.assertEqual([row['id'] for row in pools['conversation']], [key('selected')])

    def test_draft_positive_cases_cover_all_types_and_corrections_have_pair(self):
        store = self.type_balanced_store()
        def ask(prompt):
            match = re.search(r'artifact: ([a-f0-9]{32})', prompt)
            question = 'What is the serial for ' + match[1] + '?' if match else 'What does the selected evidence establish?'
            return {'content': json.dumps(dict(question=question, referenceAnswer='PRIVATE_DRAFT_REFERENCE'))}
        draft = e.draft_fixture(store, ask, 'local/fixture')
        self.assertFalse(draft['reviewed'])
        self.assertEqual(set(draft['sampling']['representedTypes']), set(e.SOURCE_TYPES))
        self.assertEqual(draft['sampling']['missingTypes'], [])
        self.assertEqual(len(draft['cases']), 30)
        correction = [row for row in draft['cases'] if row['category'] == 'correction']
        self.assertEqual(len(correction), 5)
        self.assertTrue(all(len(row['expectedSourceIds']) == 2 and row['sourceTypes'] == ['conversation'] for row in correction))
        unsupported = [row for row in draft['cases'] if not row['answerable']]
        self.assertEqual(len(unsupported), 5)
        self.assertTrue(all(row['sourceTypes'] == [] for row in unsupported))

    def test_gateway_requires_local_alias_and_never_accepts_arbitrary_url(self):
        with mock.patch.dict(os.environ, {'EVAL_TEST_KEY': 'PRIVATE_KEY'}):
            for model in ('https://outside.invalid/model', 'local/model:cloud', 'cloud/model', 'local/model-cloud'):
                with self.assertRaises(ValueError):
                    e.GatewayClient(model, key_env='EVAL_TEST_KEY')
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                e.GatewayClient('local/fixture', key_env='EVAL_TEST_KEY')

    def test_loopback_gateway_exact_model_and_fixed_profile(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def send(self, value):
                data = json.dumps(value).encode(); self.send_response(200)
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
            def do_GET(self):
                received.append((self.path, self.headers.get('Authorization')))
                self.send({'data': [{'id': 'local/fixture'}]})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(body)
                self.send(dict(model='local/fixture', choices=[{'message': {'content': 'PRIVATE_LOCAL_ANSWER'}}], sources=[{'id': key('source')}]))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with mock.patch.dict(os.environ, {'EVAL_TEST_KEY': 'PRIVATE_KEY'}):
                client = e.GatewayClient('local/fixture', server.server_port, 'EVAL_TEST_KEY')
                answer = client.ask('PRIVATE_QUESTION')
            self.assertEqual(answer['sourceIds'], [key('source')])
            self.assertEqual(received[1]['max_tokens'], 4096)
            self.assertFalse(received[1]['stream'])
            self.assertEqual(received[1]['model'], 'local/fixture')
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_cli_error_is_content_free(self):
        output, error = io.StringIO(), io.StringIO()
        with mock.patch.object(e, 'open_store', side_effect=RuntimeError('PRIVATE_KEY PRIVATE_CORPUS')), redirect_stdout(output), redirect_stderr(error):
            result = e.main(['--store', str(self.root / 'unused'), '--output', str(self.root / 'report'), '--fixture', str(self.root / 'unused.json')])
        self.assertEqual(result, 1)
        self.assertNotIn('PRIVATE_', output.getvalue() + error.getvalue())
        self.assertEqual(json.loads(error.getvalue())['errorCode'], 'RuntimeError')


if __name__ == '__main__':
    unittest.main()
