"""Synthetic history intake/adapters; never reads a user's export."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location('history_import_subject', Path(__file__).with_name('interlinked_history_import.py'))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def j(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


def message(mid, text, role='user', content_type='text', metadata=None):
    return dict(id=mid, author=dict(role=role), content=dict(content_type=content_type, parts=[text]), create_time=1730000000, metadata=metadata or {})


def conversation(cid='fixture', restricted=False):
    return dict(id=cid, title='Synthetic notebook', create_time=1730000000, current_node='c', is_do_not_remember=restricted,
                mapping={'root': dict(parent=None, message=None),
                         'a': dict(parent='root', message=message('ma', 'The workshop uses amber labels.', metadata={'attachment': {'file_id': 'file_fixture'}})),
                         'b': dict(parent='a', message=message('mb', 'An alternative assistant answer.', 'assistant')),
                         'c': dict(parent='a', message=message('mc', 'A confirmed user correction.')),
                         'd': dict(parent='c', message=message('md', 'Private inference fixture.', 'assistant', 'thoughts'))})


def ooxml(kind, invalid=False):
    stream = io.BytesIO()
    main = {'docx': 'word/document.xml', 'xlsx': 'xl/workbook.xml', 'pptx': 'ppt/presentation.xml'}[kind]
    mime = {'docx': 'wordprocessingml.document.main+xml', 'xlsx': 'spreadsheetml.sheet.main+xml', 'pptx': 'presentationml.presentation.main+xml'}[kind]
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types><Override PartName="/' + main + '" ContentType="' + ('wrong' if invalid else mime) + '"/></Types>')
        if kind == 'docx':
            archive.writestr(main, '<document><p><t>Document fixture sentence.</t></p></document>')
        elif kind == 'xlsx':
            archive.writestr(main, '<workbook xmlns:r="urn:rels"><sheets><sheet name="Fixture sheet" r:id="r1"/></sheets></workbook>')
            archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr('xl/worksheets/sheet1.xml', '<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Fixture cell</t></is></c><c r="B1"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>')
        else:
            archive.writestr(main, '<presentation/>')
            archive.writestr('ppt/slides/slide1.xml', '<slide><t>Fixture slide.</t></slide>')
            archive.writestr('ppt/notesSlides/notesSlide1.xml', '<notes><t>Fixture notes.</t></notes>')
    return stream.getvalue()


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-history-import-tests-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'input'
        self.source.mkdir()
        self.stage = self.root / 'stage'

    def tearDown(self):
        self.temp.cleanup()

    def export(self, files=None, conversations=None):
        files = dict(files or {})
        files.setdefault('conversations-000.json', j(conversations if conversations is not None else [conversation()]))
        files.setdefault('file_fixture.dat', b'Attachment fixture text.')
        files.setdefault('conversation_asset_file_names.json', j({'file_fixture.dat': 'fixture.txt'}))
        for rel, data in files.items():
            p = self.source / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        rows = [dict(path=rel, size_bytes=len(data)) for rel, data in files.items()]
        logical = {rel: dict(files=[rel], sharded=False) for rel in files if not rel.startswith('conversations-')}
        shards = sorted(rel for rel in files if rel.startswith('conversations-'))
        logical['conversations.json'] = dict(files=shards, shard_count=len(shards), sharded=True)
        manifest = dict(version=1, export_files=rows, logical_files=logical, manifest_file='export_manifest.json')
        (self.source / 'export_manifest.json').write_bytes(j(manifest))
        return manifest

    def parse(self):
        receipt = h.stage_export(self.source, self.stage)
        coverage = {}
        records = list(h.iter_records(receipt['stagedRoot'], coverage))
        return receipt, records, coverage

    def test_stage_verifies_source_preservation_and_idempotency(self):
        self.export()
        before = {p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        first = h.stage_export(self.source, self.stage)
        second = h.stage_export(self.source, self.stage)
        self.assertEqual(first, second)
        self.assertEqual(before, {p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        self.assertTrue(first['complete'])
        self.assertEqual(first['files'], len(before))
        self.assertTrue(all(len(row['sha256']) == 64 for row in first['entries']))

    def test_cancel_and_resume_checkpoint(self):
        self.export()
        cancel = threading.Event()
        with self.assertRaises(h.Cancelled):
            h.stage_export(self.source, self.stage, cancel=cancel, progress=lambda _: cancel.set())
        self.assertFalse((self.stage / 'RECEIPT.json').exists())
        cancel.clear()
        result = h.stage_export(self.source, self.stage, cancel=cancel)
        self.assertTrue(result['complete'])

    def test_source_change_after_early_copy_refused(self):
        self.export()
        def change_after_manifest(progress):
            if progress['completed'] == 1:
                path = self.source / 'export_manifest.json'
                path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            h.stage_export(self.source, self.stage, progress=change_after_manifest)
        self.assertFalse((self.stage / 'RECEIPT.json').exists())

    def test_recovery_when_file_published_before_checkpoint(self):
        self.export()
        h.stage_export(self.source, self.stage)
        checkpoint = json.loads((self.stage / 'CHECKPOINT.json').read_text())
        checkpoint['completed'].pop('file_fixture.dat')
        (self.stage / 'CHECKPOINT.json').write_text(json.dumps(checkpoint))
        self.assertTrue(h.stage_export(self.source, self.stage)['complete'])

    def test_manifest_pin_refuses_changed_source(self):
        self.export()
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            h.stage_export(self.source, self.stage, expected_manifest_sha256='0' * 64)

    def test_size_mismatch_and_extra_file_refused(self):
        self.export()
        (self.source / 'file_fixture.dat').write_bytes(b'Changed length.')
        with self.assertRaises(ValueError):
            h.stage_export(self.source, self.stage)
        (self.source / 'extra.txt').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            h.stage_export(self.source, self.root / 'stage2')

    def test_manifest_path_traversal_and_case_collision(self):
        manifest = self.export()
        manifest['export_files'][0]['path'] = '../outside.txt'
        (self.source / 'export_manifest.json').write_bytes(j(manifest))
        with self.assertRaisesRegex(ValueError, 'relative path'):
            h.stage_export(self.source, self.stage)
        manifest = self.export()
        manifest['export_files'].append(dict(path='FILE_FIXTURE.DAT', size_bytes=1))
        (self.source / 'export_manifest.json').write_bytes(j(manifest))
        with self.assertRaisesRegex(ValueError, 'case-colliding'):
            h.stage_export(self.source, self.root / 'stage2')

    def test_symlink_source_refused(self):
        self.export()
        link = self.root / 'linked'
        try:
            link.symlink_to(self.source, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Host does not permit a synthetic symlink.')
        with self.assertRaises(ValueError):
            h.stage_export(link, self.stage)

    def test_zip_export_roundtrip(self):
        self.export()
        zipped = self.root / 'export.zip'
        with zipfile.ZipFile(zipped, 'w', zipfile.ZIP_DEFLATED) as archive:
            for p in self.source.iterdir():
                archive.write(p, p.name)
        result = h.stage_export(zipped, self.stage)
        self.assertTrue(result['complete'])
        self.assertEqual(len(list(h.iter_records(result['stagedRoot'], {}))), 4)

    def test_zip_escape_refused_without_writing_target(self):
        self.export()
        zipped = self.root / 'export.zip'
        with zipfile.ZipFile(zipped, 'w') as archive:
            archive.writestr('../escaped.txt', 'no')
            archive.writestr('export_manifest.json', (self.source / 'export_manifest.json').read_bytes())
        with self.assertRaises(ValueError):
            h.stage_export(zipped, self.stage)
        self.assertFalse((self.root / 'escaped.txt').exists())

    def test_parse_refuses_mutated_staged_file(self):
        self.export()
        receipt = h.stage_export(self.source, self.stage)
        (Path(receipt['stagedRoot']) / 'file_fixture.dat').write_bytes(b'x' * len(b'Attachment fixture text.'))
        with self.assertRaisesRegex(ValueError, 'changed'):
            list(h.iter_records(receipt['stagedRoot'], {}))

    def test_parent_branches_current_path_and_archival_records(self):
        self.export()
        _, records, coverage = self.parse()
        chats = [r for r in records if r['kind'] == 'conversation']
        self.assertEqual(coverage['conversations'], 1)
        self.assertEqual(coverage['message_records'], 4)
        self.assertEqual(coverage['branch_points'], 1)
        self.assertEqual(coverage['branched_conversations'], 1)
        self.assertEqual(coverage['archival_message_records'], 1)
        self.assertEqual([r['metadata']['onCurrentBranch'] for r in chats], [True, False, True])
        self.assertEqual([r['memory_allowed'] for r in chats], [True, False, True])
        self.assertFalse(any('Private inference fixture.' in r['text'] for r in records))

    def test_restriction_follows_attachment_lineage(self):
        self.export(conversations=[conversation(restricted=True)])
        _, records, coverage = self.parse()
        self.assertEqual(coverage['do_not_remember_conversations'], 1)
        self.assertTrue(all(not r['memory_allowed'] for r in records))
        attachment = next(r for r in records if r['kind'] == 'document')
        self.assertEqual(attachment['metadata']['conversationIds'], ['fixture'])
        self.assertEqual(len(attachment['restricted_source_ids']), 1)

    def test_duplicate_message_id_in_different_nodes_keeps_branches(self):
        record = conversation()
        record['mapping']['b']['message']['id'] = 'ma'
        self.export(conversations=[record])
        _, records, _ = self.parse()
        chats = [r for r in records if r['kind'] == 'conversation']
        self.assertEqual(len({r['id'] for r in chats}), 3)

    def test_cycle_outside_selected_branch_refused(self):
        record = conversation()
        record['mapping']['b']['parent'] = 'b'
        self.export(conversations=[record])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            self.parse()

    def test_group_speaker_remains_unspecified(self):
        group = dict(chats=[dict(id='g1', name='Group fixture', messages=[dict(id='gm1', role='user', text='Group fixture statement.', created_at='2026-01-01T12:00:00Z', attachments=[])])])
        self.export({'group_chats.json': j(group)})
        _, records, coverage = self.parse()
        record = next(r for r in records if r['kind'] == 'group')
        self.assertEqual(record['metadata']['speaker'], 'unspecified')
        self.assertFalse(record['memory_allowed'])
        self.assertEqual(coverage['group_messages'], 1)

    def test_page_sequence_coverage(self):
        page = dict(includes_current_sequence=True, head=dict(page_id='p1', current_sequence=4, revision=2, checkpoint_id='cp'), projection=dict(page_id='p1', title='Page fixture', materialized_search_text='Readable page.', projection_through_sequence=4))
        stale = json.loads(json.dumps(page)); stale['projection']['projection_through_sequence'] = 3
        self.export({'pages/p1/current.json': j(page), 'pages/p2/current.json': j(stale)})
        _, records, coverage = self.parse()
        self.assertEqual(len([r for r in records if r['kind'] == 'page']), 1)
        self.assertEqual(coverage['issues']['stale_page_projection'], 1)

    def test_dat_ooxml_and_spreadsheet_formula_passive(self):
        names = {'file_fixture.dat': 'fixture.txt', 'file_doc.dat': 'report.docx', 'file_sheet.dat': 'sheet.xlsx', 'file_slide.dat': 'slides.pptx'}
        self.export({'file_doc.dat': ooxml('docx'), 'file_sheet.dat': ooxml('xlsx'), 'file_slide.dat': ooxml('pptx'), 'conversation_asset_file_names.json': j(names)})
        _, records, _ = self.parse()
        docs = [r for r in records if r['kind'] == 'document']
        self.assertTrue(any('Document fixture sentence.' in r['text'] for r in docs))
        cell = next(r for r in docs if 'cached value: 2' in r['text'])
        self.assertEqual(cell['metadata']['sheet'], 'Fixture sheet')
        self.assertEqual(cell['metadata']['cells'], ['A1', 'B1'])
        self.assertEqual(len([r for r in docs if r['metadata'].get('slide') == 1]), 2)

    def test_invalid_ooxml_type_and_xml_entities_reported(self):
        coverage = {}
        self.assertEqual(list(h._document(ooxml('docx', invalid=True), 'fixture.docx', coverage)), [])
        self.assertEqual(coverage['issues']['invalid_document_container'], 1)
        with self.assertRaises(ValueError):
            h._xml(b'<!DOCTYPE doc [<!ENTITY e SYSTEM "file:///private">]><doc>&e;</doc>')

    def test_html_scripts_not_indexed(self):
        coverage = {}
        result = list(h._document(b'<html><p>Visible fixture.</p><script>hiddenScript()</script></html>', 'fixture.html', coverage))
        self.assertIn('Visible fixture.', result[0][1])
        self.assertNotIn('hiddenScript', result[0][1])

    def test_sites_sql_metadata_and_omission_counts(self):
        self.export({'sites/site/meta.json': j(dict(corpus='site', metadata=dict(title='Site fixture', description='Static fixture.'))), 'sites/site/data.sql': b'CREATE TABLE fixture(value TEXT);', 'sites/manifest.json': j(dict(omissions=[{}, {}], redactions=[{}], inventory_warnings=[], incomplete_corpora=[]))})
        _, records, coverage = self.parse()
        self.assertEqual(coverage['site_omissions'], 2)
        self.assertEqual(coverage['site_redactions'], 1)
        self.assertTrue(any(r['metadata'].get('passiveSql') for r in records))

    def test_git_bundle_is_read_without_checkout(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('Git unavailable.')
        repo = self.root / 'gitfixture'; repo.mkdir()
        def run(*args):
            subprocess.run([git, *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        run('init', '--quiet')
        (repo / 'fixture.py').write_text('answer = "source fixture"\n')
        run('add', 'fixture.py')
        run('-c', 'user.email=fixture@example.invalid', '-c', 'user.name=Fixture', 'commit', '-m', 'fixture', '--quiet')
        bundle = self.root / 'fixture.bundle'; run('bundle', 'create', str(bundle), '--all')
        self.export({'sites/site/source.bundle': bundle.read_bytes()})
        _, records, coverage = self.parse()
        self.assertEqual(coverage['site_source_segments'], 1)
        result = next(r for r in records if r['metadata'].get('gitPath'))
        self.assertEqual(result['metadata']['gitPath'], 'fixture.py')
        self.assertIn('source fixture', result['text'])

    def test_streaming_array_unicode_and_malformed_inputs(self):
        array = self.root / 'array.json'
        array.write_text(json.dumps([{'text': 'é' * 70000}, {'text': '尾'}], ensure_ascii=False), encoding='utf-8')
        self.assertEqual(len(list(h._iter_array(array))), 2)
        for value in ('[1,]', '[1 2]', '[1', '{}', '[1] trailing'):
            array.write_text(value, encoding='utf-8')
            with self.assertRaises(ValueError):
                list(h._iter_array(array))

    def test_same_logical_record_changes_revision_only(self):
        first = h._record('fixed', 'conversation', 'Fixture', 'Old', memory_allowed=True)
        second = h._record('fixed', 'conversation', 'Fixture', 'New', memory_allowed=False)
        self.assertEqual(first['id'], second['id'])
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(len(first['revision']), 64)

    def test_missing_pdf_dependency_is_coverage_not_silent_success(self):
        coverage = {}
        with mock.patch.object(h.importlib.util, 'find_spec', return_value=None):
            self.assertEqual(list(h._document(b'%PDF-1.7\nfixture', 'fixture.pdf', coverage)), [])
        self.assertEqual(coverage['issues']['pdf_dependency_unavailable'], 1)

    def test_guarded_source_identity_folder_zip_and_cancel(self):
        self.export()
        expected = hashlib.sha256((self.source / 'export_manifest.json').read_bytes()).hexdigest()
        self.assertEqual(h.source_identity(self.source), expected)
        packed = self.root / 'export.zip'
        with zipfile.ZipFile(packed, 'w') as archive:
            for path in self.source.iterdir():
                archive.write(path, path.name)
        self.assertEqual(h.source_identity(packed), expected)
        stop = threading.Event(); stop.set()
        with self.assertRaises(h.Cancelled):
            h.source_identity(self.source, cancel=stop)

    def test_long_destination_and_near_limit_filename_resume(self):
        # Both full-path length and component length must survive staging. The
        # temporary filename must not append an identifier to a long basename.
        rel = 'a' * 235 + '.dat'
        self.export()
        data = b'Long path fixture passage.'
        with open(h._files.fs(self.source / rel), 'xb') as stream:
            stream.write(data)
        manifest = json.loads((self.source / 'export_manifest.json').read_bytes())
        manifest['export_files'].append(dict(path=rel, size_bytes=len(data)))
        manifest['logical_files'][rel] = dict(files=[rel], sharded=False)
        (self.source / 'export_manifest.json').write_bytes(j(manifest))
        destination = self.stage / ('deep-' * 30) / ('nested-' * 20)
        try:
            receipt = h.stage_export(self.source, destination)
            self.assertTrue(receipt['complete'])
            self.assertEqual(h.stage_export(self.source, destination)['entries'], receipt['entries'])
            coverage = {}
            records = list(h.iter_records(receipt['stagedRoot'], coverage))
            self.assertTrue(any(r['text'] == data.decode() for r in records))
        finally:
            # Extended I/O spelling is needed for cleanup on Windows too.
            shutil.rmtree(h._files.fs(self.root))

    def test_worksheet_streams_beyond_whole_member_text_limit(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(ooxml('xlsx'))) as original, zipfile.ZipFile(payload, 'w') as result:
            for name in original.namelist():
                data = original.read(name)
                if name == 'xl/worksheets/sheet1.xml':
                    rows = ''.join('<row r="%d"><c r="A%d"><f>1+1</f><v>2</v></c><c r="B%d"/></row>' % (n, n, n) for n in range(1, 101))
                    rows += '<row r="101"><c r="A101"/></row>'
                    data = ('<worksheet><sheetData>' + rows + '</sheetData></worksheet>').encode()
                result.writestr(name, data)
        with mock.patch.object(h, 'MAX_TEXT', 1024), zipfile.ZipFile(io.BytesIO(payload.getvalue())) as archive:
            rows = list(h._ooxml(archive, 'xlsx'))
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[-1][2]['cells'], ['A100'])
        self.assertIn('formula: =1+1; cached value: 2', rows[-1][1])

    def test_streamed_xml_rejects_active_and_oversized_records(self):
        values = [b'<sheet><row>' + b'x' * 4096 + b'</row></sheet>',
                  '<!DOCTYPE sheet [<!ENTITY secret "forbidden">]><sheet><row>&secret;</row></sheet>'.encode('utf-16')]
        for value in values:
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w') as archive:
                archive.writestr('sheet.xml', value)
            with mock.patch.object(h, 'MAX_TEXT', 1024), zipfile.ZipFile(payload) as archive:
                with self.assertRaises(ValueError):
                    list(h._xml_elements(archive, 'sheet.xml', 'row'))


if __name__ == '__main__':
    unittest.main()
