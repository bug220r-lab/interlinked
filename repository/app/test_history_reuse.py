"""Explicit prepared-vector reuse; synthetic private inputs, no model/server."""
import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
from test_history_store import h, record, CountingEmbedder, np


@unittest.skipIf(np is None, 'NumPy fixture runtime unavailable')
class PreparedReuse(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='history-reuse-')
        self.root = Path(self.temp.name)
        self.embed = CountingEmbedder()
        self.store = h.HistoryStore(self.root / 'archive', embedder=self.embed)
        self.rows = [record('original', 'owl original fact')]
        self.base = self.store.build(self.rows)
        self.path = self.store.root / 'generations' / self.base['generationId']

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def proof(self, policy='sealed-vector-format-v1'):
        return dict(generationId=self.base['generationId'], processingPolicy=policy,
                    metadataSha256=h.file_hash(self.path / 'generation.json'),
                    indexSha256=h.file_hash(self.path / 'index.sqlite3'),
                    vectorsSha256=h.file_hash(self.path / 'vectors.npy'))

    def test_only_new_passages_embed_without_activation_or_donor_change(self):
        proof = self.proof()
        before = self.embed.documents
        result = self.store.build(self.rows + [record('site', 'new site source')], reuse_generation=proof)
        self.assertEqual(result['reusedVectors'], self.base['chunks'])
        self.assertEqual(self.embed.documents - before, result['newVectors'])
        self.assertEqual(result['newVectors'], 1)
        self.assertFalse((self.store.root / 'ACTIVE.json').exists())
        self.assertEqual(proof, self.proof())
        data = h.json_read(self.store.root / 'generations' / result['generationId'] / 'BUILD.json')
        self.assertEqual(data['info']['vectorReuseProof'], proof)

    def test_active_generation_retention_remains_separate_from_donor(self):
        other = self.store.build([record('active', 'active retained text')])
        self.store.activate(other['generationId'])
        result = self.store.build([record('new', 'new text')], reuse_generation=self.proof())
        self.assertEqual(result['records'], 2)
        self.assertEqual(result['retainedAbsent'], 1)
        self.assertEqual(self.store.generation_info()['generationId'], other['generationId'])
        self.assertIsNone(self.store.source(self.rows[0]['id'], generation_id=result['generationId']))

    def test_legacy_compatibility_requires_explicit_policy_and_keeps_metadata(self):
        info = h.json_read(self.path / 'generation.json')
        info.pop('vectorFormat')
        h.json_write(self.path / 'generation.json', info)
        with self.assertRaisesRegex(ValueError, 'processing format'):
            self.store.build(self.rows, reuse_generation=self.proof())
        proof = self.proof('reviewed-r4-vector-format-v1')
        result = self.store.build(self.rows, reuse_generation=proof)
        self.assertEqual(result['reusedVectors'], 1)
        self.assertEqual(result['newVectors'], 0)
        self.assertEqual(proof, self.proof('reviewed-r4-vector-format-v1'))

    def test_no_implicit_prepared_donor(self):
        result = self.store.build(self.rows + [record('new', 'new source')])
        self.assertEqual(result['reusedVectors'], 0)
        self.assertEqual(result['newVectors'], 2)

    def test_reuse_lookup_work_scales_with_rows_without_mutating_donor(self):
        rows = [record('source-' + str(i), 'unique source ' + str(i)) for i in range(1024)]
        self.base = self.store.build(rows)
        self.path = self.store.root / 'generations' / self.base['generationId']
        proof = self.proof()
        original = sqlite3.connect
        steps = [0]
        def counted_connect(database, *args, **kwargs):
            db = original(database, *args, **kwargs)
            if str(database).startswith((self.path / 'index.sqlite3').as_uri() + '?'):
                def progress():
                    steps[0] += 1000
                    return 0
                db.set_progress_handler(progress, 1000)
            return db
        with mock.patch.object(h.sqlite3, 'connect', side_effect=counted_connect):
            result = self.store.build(rows, reuse_generation=proof)
        self.assertEqual(result['reusedVectors'], 1024)
        self.assertEqual(result['newVectors'], 0)
        # A repeated unindexed scan exceeds three million VM steps here. This
        # generous deterministic work budget also includes donor validation.
        self.assertLess(steps[0], 500000)
        self.assertEqual(proof, self.proof())

    def test_bad_proof_and_identity_fail_before_embedding(self):
        for key, value in [('generationId', '../unsafe'), ('metadataSha256', '0' * 64),
                           ('processingPolicy', 'trust-missing-format')]:
            proof = dict(self.proof(), **{key: value})
            before = self.embed.documents
            with self.assertRaises(ValueError):
                self.store.build(self.rows, reuse_generation=proof)
            self.assertEqual(self.embed.documents, before)
        self.store.embedder = CountingEmbedder('a' * 64)
        with self.assertRaisesRegex(ValueError, 'embedding identity'):
            self.store.build(self.rows, reuse_generation=self.proof())
        self.assertEqual(self.store.embedder.documents, 0)

    def test_cancel_during_lookup_preparation_closes_donor_and_allows_retry(self):
        proof = self.proof()
        event = threading.Event()
        closed = []
        original = sqlite3.connect
        class CancelLookup(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                result = super().execute(sql, *args, **kwargs)
                if sql.startswith('CREATE TEMP TABLE vector_lookup'):
                    self.lookup_started = True
                    event.set()
                return result
            def close(self):
                if getattr(self, 'lookup_started', False):
                    closed.append(True)
                super().close()
        def connect(database, *args, **kwargs):
            if str(database).startswith((self.path / 'index.sqlite3').as_uri() + '?'):
                kwargs['factory'] = CancelLookup
            return original(database, *args, **kwargs)
        with mock.patch.object(h.sqlite3, 'connect', side_effect=connect):
            with self.assertRaises(InterruptedError):
                self.store.build(self.rows, reuse_generation=proof, cancel=event)
        self.assertEqual(closed, [True])
        self.assertEqual(proof, self.proof())
        self.assertFalse((self.store.root / 'ACTIVE.json').exists())
        result = self.store.build(self.rows, reuse_generation=proof)
        self.assertEqual(result['reusedVectors'], 1)
        self.assertEqual(result['newVectors'], 0)

    def test_sealed_nonfinite_rows_reject(self):
        values = np.load(self.path / 'vectors.npy', mmap_mode='r+')
        values[0, 0] = np.nan
        values.flush()
        del values
        info = h.json_read(self.path / 'generation.json')
        info['vectorsSha256'] = h.file_hash(self.path / 'vectors.npy')
        h.json_write(self.path / 'generation.json', info)
        with self.assertRaisesRegex(ValueError, 'invalid or unnormalized'):
            self.store.build(self.rows, reuse_generation=self.proof())

    def test_duplicate_vector_mapping_rejects_even_with_resealed_index(self):
        self.base = self.store.build(self.rows + [record('second', 'second source')])
        self.path = self.store.root / 'generations' / self.base['generationId']
        with closing(sqlite3.connect(self.path / 'index.sqlite3')) as db:
            source_id, revision = db.execute('SELECT id,revision FROM vector_ids WHERE position=0').fetchone()
            db.execute('UPDATE vector_ids SET id=?,revision=? WHERE position=1', (source_id, revision))
            db.commit()
        info = h.json_read(self.path / 'generation.json')
        info['indexSha256'] = h.file_hash(self.path / 'index.sqlite3')
        h.json_write(self.path / 'generation.json', info)
        with self.assertRaisesRegex(ValueError, 'position map'):
            self.store.build(self.rows, reuse_generation=self.proof())

    def test_same_size_changed_payload_rejects_reviewed_hash(self):
        proof = self.proof()
        path = self.path / 'vectors.npy'
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'reviewed proof'):
            self.store.build(self.rows, reuse_generation=proof)

    def test_resealed_schema_cannot_hide_duplicate_positions_or_chunks(self):
        for case in ('duplicate-positions', 'noninteger-position', 'duplicate-chunks'):
            with self.subTest(case=case):
                rows = [record('car', 'car evidence ' + case), record('owl', 'owl evidence'),
                        record('other', 'unrelated evidence')]
                self.base = self.store.build(rows)
                self.path = self.store.root / 'generations' / self.base['generationId']
                with closing(sqlite3.connect(self.path / 'index.sqlite3')) as db:
                    if case == 'duplicate-chunks':
                        original = db.execute('SELECT * FROM chunks ORDER BY id').fetchall()
                        db.execute('ALTER TABLE chunks RENAME TO old_chunks')
                        db.execute('CREATE TABLE chunks(id TEXT,source_id TEXT,revision TEXT,start INTEGER,end INTEGER,text TEXT)')
                        db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                                       [original[0], original[0], original[1]])
                        db.execute('DROP TABLE old_chunks')
                    else:
                        original = db.execute('SELECT * FROM vector_ids ORDER BY position').fetchall()
                        db.execute('ALTER TABLE vector_ids RENAME TO old_vector_ids')
                        db.execute('CREATE TABLE vector_ids(position,id TEXT,revision TEXT)')
                        replacement = 0 if case == 'duplicate-positions' else 1.5
                        db.executemany('INSERT INTO vector_ids VALUES(?,?,?)',
                                       [(replacement if i == 1 else i, row[1], row[2])
                                        for i, row in enumerate(original)])
                        db.execute('DROP TABLE old_vector_ids')
                    db.commit()
                info = h.json_read(self.path / 'generation.json')
                info['indexSha256'] = h.file_hash(self.path / 'index.sqlite3')
                h.json_write(self.path / 'generation.json', info)
                before = self.embed.documents
                with self.assertRaisesRegex(ValueError, 'position map'):
                    self.store.build(rows, reuse_generation=self.proof())
                self.assertEqual(self.embed.documents, before)
                self.assertFalse((self.store.root / 'ACTIVE.json').exists())

    def test_node_import_freezes_explicit_proof_before_background_start(self):
        import interlinked_history_service as service
        import interlinked_history_import as importer
        callbacks = []
        node = service.HistoryNode(self.root / 'node', object())
        try:
            transfer = node._transfer('a' * 64)
            transfer.mkdir(parents=True)
            (transfer / 'transfer.json').write_text('{"files":[]}')
            proof = self.proof()
            expected = copy.deepcopy(proof)
            with (mock.patch.object(node.jobs, 'start', side_effect=lambda kind, callback: callbacks.append(callback)),
                  mock.patch.object(importer, 'stage_export', return_value={'stagedRoot':str(self.root)}),
                  mock.patch.object(importer, 'iter_records', return_value=iter(self.rows)),
                  mock.patch.object(service, 'Embedder', return_value=self.embed),
                  mock.patch.object(node.store, 'build', return_value={}) as build):
                node.execute('import', {'id':'a' * 64, 'reuseGeneration':proof})
                proof['generationId'] = 'b' * 32
                callbacks[0](threading.Event(), lambda _:None)
                self.assertEqual(build.call_args.kwargs['reuse_generation'], expected)
        finally:
            node.close()

    def test_donor_proof_distinguishes_identical_input_checkpoints(self):
        first = self.store.build(self.rows, reuse_generation=self.proof())
        # A different immutable metadata seal must not resume the former proof.
        info = h.json_read(self.path / 'generation.json')
        info['reviewNote'] = 'synthetic second explicit proof'
        h.json_write(self.path / 'generation.json', info)
        second = self.store.build(self.rows, reuse_generation=self.proof())
        self.assertNotEqual(first['generationId'], second['generationId'])
        self.assertFalse((self.store.root / 'ACTIVE.json').exists())

    def test_cancelled_reuse_resumes_same_provenance(self):
        rows = [record(str(i), 'owl source ' + str(i)) for i in range(70)]
        cancel = threading.Event()
        proof = self.proof()
        def progress(value):
            if value.get('phase') == 'embedding':
                cancel.set()
        with self.assertRaises(InterruptedError):
            self.store.build(rows, reuse_generation=proof, cancel=cancel, progress=progress)
        result = self.store.build(rows, reuse_generation=proof)
        self.assertGreater(result.get('resumedVectors', 0), 0)
        self.assertEqual(h.json_read(self.store.root / 'generations' / result['generationId'] / 'generation.json')['vectorReuseProof'], proof)


if __name__ == '__main__':
    unittest.main()
