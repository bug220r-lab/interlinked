"""History persistence/retrieval tests use synthetic records and private temp roots."""
import copy
import hashlib
import importlib.util
import json
import re
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('history_store_tests_subject', Path(__file__).with_name('interlinked_history_store.py'))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

try:
    import numpy as np
except ImportError:
    np = None


def key(value):
    return hashlib.sha256(value.encode()).hexdigest()


def record(name, text, **extra):
    value = dict(id=key(name), revision=key(text), kind='message', title='Synthetic fixture', text=text,
                 timestamp='2026-11-02T00:00:00Z', project='fixture', memory_allowed=True,
                 metadata=dict(conversationId=name, role='user'))
    value.update(extra)
    return value


class Embedder:
    identity = dict(model='synthetic-test-only', digest='f' * 64, dimensions=3)

    def __call__(self, texts):
        # Synthetic semantic space lets a synonym retrieve a dissimilar phrase.
        return [[1, 0, 0] if set(re.findall(r'\w+', text)).intersection({'car', 'automobile'}) else
                [0, 1, 0] if 'owl' in text else [0, 0, 1] for text in texts]

    def query(self, text):
        return self([text])[0]


class CountingEmbedder(Embedder):
    def __init__(self, digest='f' * 64):
        self.identity = dict(type(self).identity, digest=digest)
        self.documents = 0

    def __call__(self, texts):
        self.documents += len(texts)
        return super().__call__(texts)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='interlinked-history-store-')
        self.root = Path(self.temp.name)
        self.store = h.HistoryStore(self.root / 'primary')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def build(self, rows, store=None):
        store = store or self.store
        result = store.build(iter(rows), receipt={'private': 'PRIVATE_CANARY'}, coverage={'supported': len(rows)})
        store.activate(result['generationId'])
        return result

    def test_build_is_not_activation_and_state_has_no_corpus_or_receipt(self):
        row = record('one', 'PRIVATE_CANARY owl repair')
        built = self.store.build([row], receipt={'private': 'PRIVATE_CANARY'})
        self.assertFalse(self.store.status()['available'])
        self.assertEqual(self.store.search('owl'), [])
        self.store.activate(built['generationId'])
        found = self.store.search('owl')
        self.assertEqual(found[0]['sourceId'], row['id'])
        self.assertEqual(self.store.source(row['id'])['text'], row['text'])
        reduced = json.dumps(self.store.status())
        self.assertNotIn('PRIVATE_CANARY', reduced)
        self.assertNotIn('Synthetic fixture', reduced)

    def test_revisions_and_absence_are_retained_and_reimport_idempotent(self):
        first = record('one', 'owl first')
        missing = record('two', 'owl retained')
        self.build([first, missing])
        new = record('one', 'owl corrected')
        result = self.build([new])
        self.assertEqual(result['records'], 2)
        self.assertEqual(result['revisions'], 3)
        self.assertEqual(result['retainedAbsent'], 1)
        self.assertEqual(self.store.source(first['id'], first['revision'])['text'], first['text'])
        self.assertTrue(self.store.source(missing['id'])['retainedAbsent'])
        result = self.build([new])
        self.assertEqual(result['revisions'], 3)

    def test_exclusion_covers_conversation_attachment_and_survives_reimport(self):
        a = record('a', 'PRIVATE_CANARY owl', metadata={'conversationId': 'shared', 'role': 'user'})
        b = record('b', 'PRIVATE_CANARY owl second', metadata={'conversationId': 'shared', 'role': 'assistant'})
        asset = record('asset', 'PRIVATE_CANARY owl attachment', kind='attachment', metadata={'assetId': 'asset', 'conversationIds': ['shared']})
        public = record('public', 'owl public')
        self.build([a, b, asset, public])
        self.store.exclude(a['id'])
        self.assertEqual([row['id'] for row in self.store.search('owl')], [public['id']])
        self.assertIsNone(self.store.source(asset['id']))
        self.build([a, b, asset, public])
        self.assertIsNone(self.store.source(b['id']))
        self.store.exclude(a['id'], False)
        self.assertIsNotNone(self.store.source(b['id']))

    def test_document_exclusion_covers_asset_without_hiding_whole_conversation(self):
        message = record('message', 'owl message', metadata={'conversationId': 'shared'})
        asset = record('asset', 'owl document', kind='document', metadata={'assetId': 'asset', 'conversationIds': ['shared']})
        segment = record('segment', 'owl second page', kind='document', metadata={'assetId': 'asset', 'conversationIds': ['different']})
        self.build([message, asset, segment])
        self.store.exclude(asset['id'])
        self.assertIsNotNone(self.store.source(message['id']))
        self.assertIsNone(self.store.source(segment['id']))

    def test_chunks_preserve_exact_unicode_offsets_and_late_matches(self):
        text = ('🦉 A synthetic paragraph.\n' * 150) + 'UNIQUE_LATE_MATCH'
        row = record('long', text)
        built = self.build([row])
        self.assertGreater(built['chunks'], 1)
        for start, end, piece in h.chunks(text):
            self.assertEqual(piece, text[start:end])
            self.assertLessEqual(len(piece.encode('utf-8')), h.CHUNK_BYTES)
        result = self.store.search('UNIQUE_LATE_MATCH')[0]
        self.assertEqual(result['sourceId'], row['id'])
        self.assertIn('UNIQUE_LATE_MATCH', result['excerpt'])
        self.assertEqual(result['excerpt'], text[result['start']:result['end']])
        self.assertEqual(self.store.source(row['id'])['text'], text)

    def test_memory_requires_review_and_eligible_current_sources(self):
        safe = record('safe', 'Prefers owl diagrams')
        dnr = record('dnr', 'Never memorize this owl', memory_allowed=False)
        group = record('group', 'Unknown group speaker', kind='group_message')
        self.build([safe, dnr, group])
        self.assertEqual([row['id'] for row in self.store.memory_inputs()], [safe['id']])
        for row in (dnr, group):
            with self.assertRaises(ValueError):
                self.store.propose_memory('Restricted', [row['id']])
        proposal = self.store.propose_memory('Prefers diagrams', [safe['id']], 'preference', 'fixture')
        self.assertEqual(self.store.memories('approved'), [])
        edited = self.store.review_memory(proposal['id'], 'edit', 'Prefers owl diagrams')
        self.assertEqual(edited['effectiveStatus'], 'proposed')
        self.store.review_memory(proposal['id'], 'approve')
        self.assertEqual(len(self.store.memories('approved')), 1)
        self.store.exclude(safe['id'])
        self.assertEqual(self.store.memories('approved'), [])
        self.assertEqual(self.store.memories()[0]['effectiveStatus'], 'blocked')
        self.store.review_memory(proposal['id'], 'reject')
        with self.assertRaises(ValueError):
            self.store.review_memory(proposal['id'], 'approve')

    def test_memory_cannot_reactivate_on_new_revision_or_lost_restriction(self):
        safe = record('safe', 'owl preference')
        restricted = record('restricted', 'owl secret', memory_allowed=False)
        self.build([safe, restricted])
        proposal = self.store.propose_memory('owl preference', [safe['id']])
        self.store.review_memory(proposal['id'], 'approve')
        self.build([record('safe', 'changed owl preference'), record('restricted', 'new owl secret', memory_allowed=True)])
        self.assertEqual(self.store.memories('approved'), [])
        self.assertEqual(self.store.memories()[0]['effectiveStatus'], 'stale')
        self.assertFalse(self.store.source(restricted['id'])['memory_allowed'])

    def test_memory_requires_identified_user_for_conversation_evidence(self):
        rows = [record('speaker-' + str(index), 'Synthetic conversation evidence.', kind=kind,
                       metadata={'role': role} if role is not None else {})
                for index, (kind, role) in enumerate((('conversation', 'assistant'), ('message', None),
                                                     ('conversation_message', 'unspecified'), ('message', 'system')))]
        user = record('identified-user', 'User preference.')
        document = record('independent-document', 'Document fact.', kind='document', metadata={})
        self.build(rows + [user, document])
        self.assertEqual({row['id'] for row in self.store.memory_inputs()}, {user['id'], document['id']})
        self.assertEqual(self.store.memory_inputs([row['id'] for row in rows]), [])
        for row in rows:
            with self.assertRaisesRegex(ValueError, 'excluded or unavailable'):
                self.store.propose_memory('Must not infer a personal memory', [row['id']], request_id='a' * 32)
        self.assertEqual(self.store.propose_memory('Document fact', [document['id']])['effectiveStatus'], 'proposed')

    def test_memory_request_survives_lost_response_and_store_restart(self):
        source = record('retry-source', 'Prefers written checklists.')
        self.build([source])
        request = '1' * 32
        arguments = dict(request_id=request, expected_revisions={source['id']: source['revision']})
        with self.assertRaises(TimeoutError):
            self.store.propose_memory('Prefers checklists', [source['id']], **arguments)
            # The primary committed, but the caller did not receive its reply.
            raise TimeoutError('Synthetic response loss')
        retained = self.store.memories()[0]
        self.store.close()
        self.store = h.HistoryStore(self.root / 'primary')
        retry = self.store.propose_memory('Prefers checklists', [source['id']], **arguments)
        self.assertEqual(retry, retained)
        self.assertEqual(len(self.store.memories()), 1)
        self.assertNotIn(request, json.dumps(self.store._control_snapshot()))

    def test_memory_request_retry_preserves_edited_approved_and_rejected_state(self):
        source = record('reviewed-retry', 'Prefers diagrams.')
        self.build([source])
        arguments = dict(request_id='2' * 32, expected_revisions={source['id']: source['revision']})
        first = self.store.propose_memory('Original proposal', [source['id']], **arguments)
        self.store.review_memory(first['id'], 'edit', 'User-reviewed wording')
        approved = self.store.review_memory(first['id'], 'approve')
        self.assertEqual(self.store.propose_memory('Original proposal', [source['id']], **arguments), approved)
        rejected = self.store.review_memory(first['id'], 'reject')
        self.assertEqual(self.store.propose_memory('Original proposal', [source['id']], **arguments), rejected)
        with self.assertRaisesRegex(ValueError, 'payload differs'):
            self.store.propose_memory('User-reviewed wording', [source['id']], **arguments)
        self.assertEqual(self.store.memories(), [rejected])

    def test_memory_request_rejects_changed_payload_without_mutating_original(self):
        first, second = record('one', 'Preference one'), record('two', 'Preference two')
        self.build([first, second])
        payload = dict(text='Preference', source_ids=[first['id']], category='preference', project='fixture',
                       request_id='3' * 32, expected_revisions={first['id']: first['revision']})
        retained = self.store.propose_memory(**payload)
        changes = [dict(text='Different'), dict(category='decision'), dict(project=''),
                   dict(source_ids=[second['id']], expected_revisions={second['id']: second['revision']}),
                   dict(expected_revisions={first['id']: '0' * 64}), dict(expected_revisions=None)]
        for change in changes:
            with self.assertRaisesRegex(ValueError, 'payload differs'):
                self.store.propose_memory(**dict(payload, **change))
        self.assertEqual(self.store.memories(), [retained])

    def test_memory_request_and_proposal_commit_atomically(self):
        source = record('atomic', 'Prefers a checklist.')
        self.build([source])
        with self.store._controls() as db:
            db.execute("CREATE TRIGGER synthetic_abort BEFORE INSERT ON memory_requests BEGIN SELECT RAISE(ABORT,'synthetic interruption'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.propose_memory('Checklist', [source['id']], request_id='4' * 32)
        self.assertEqual(self.store.memories(), [])
        with self.store._controls() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memory_requests').fetchone()[0], 0)
            db.execute('DROP TRIGGER synthetic_abort')
        retry = self.store.propose_memory('Checklist', [source['id']], request_id='4' * 32)
        self.assertEqual(self.store.memories(), [retry])

    def test_memory_revision_fence_and_restricted_retry(self):
        old = record('revision-fence', 'Uses old checklist.')
        self.build([old])
        new = record('revision-fence', 'Uses new checklist.')
        self.build([new])
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            self.store.propose_memory('Uses old checklist', [old['id']], request_id='5' * 32,
                                      expected_revisions={old['id']: old['revision']})
        arguments = dict(request_id='5' * 32, expected_revisions={new['id']: new['revision']})
        proposal = self.store.propose_memory('Uses new checklist', [new['id']], **arguments)
        restricted = record('revision-fence', 'Do not remember checklist.', memory_allowed=False)
        self.build([restricted])
        replay = self.store.propose_memory('Uses new checklist', [new['id']], **arguments)
        self.assertEqual(replay['id'], proposal['id'])
        self.assertEqual(replay['effectiveStatus'], 'blocked')
        self.assertEqual(len(self.store.memories()), 1)
        with self.assertRaisesRegex(ValueError, 'excluded or unavailable'):
            self.store.propose_memory('Must not create', [new['id']], request_id='6' * 32,
                                      expected_revisions={new['id']: restricted['revision']})

    def test_legacy_memory_ids_and_request_validation_remain_compatible(self):
        source = record('legacy-memory', 'Prefers short notes.')
        self.build([source])
        first = self.store.propose_memory('Short notes', [source['id']])
        second = self.store.propose_memory('Short notes', [source['id']])
        self.assertNotEqual(first['id'], second['id'])
        self.assertRegex(first['id'], r'^[a-f0-9]{32}$')
        self.store.review_memory(first['id'], 'approve')
        for options in (dict(request_id='bad'), dict(request_id='A' * 32),
                        dict(expected_revisions={}), dict(expected_revisions={source['id']: 'bad'}),
                        dict(expected_revisions={source['id']: source['revision'], key('other'): key('other')})):
            with self.assertRaises(ValueError):
                self.store.propose_memory('Short notes', [source['id']], **options)
        self.assertEqual(len(self.store.memories()), 2)

    def test_memory_pages_prioritize_user_conversations_without_dropping_categories(self):
        document = record('document', 'Document decision.', id='0' * 63 + '1', kind='document')
        page = record('page', 'Page decision.', id='0' * 63 + '2', kind='page')
        first_user = record('first-user', 'User preference one.', id='e' * 64)
        second_user = record('second-user', 'User preference two.', id='f' * 64, kind='conversation')
        site = record('site', 'Site decision.', id='0' * 63 + '3', kind='site')
        group = record('group', 'Unknown group author.', kind='group')
        unknown = record('unknown', 'Unspecified author.', metadata={'speakerUnknown': True})
        self.build([document, page, first_user, second_user, site, group, unknown])
        first = self.store.memory_input_page(limit=2)
        self.assertEqual([row['id'] for row in first['sources']], [first_user['id'], second_user['id']])
        self.assertFalse(first['exhausted'])
        self.store.close(); self.store = h.HistoryStore(self.root / 'primary')
        second = self.store.memory_input_page(limit=2, after=first['nextCursor'])
        self.assertEqual([row['id'] for row in second['sources']], [page['id'], document['id']])
        third = self.store.memory_input_page(limit=2, after=second['nextCursor'])
        self.assertEqual([row['id'] for row in third['sources']], [site['id']])
        self.assertTrue(third['exhausted'])
        self.assertIsNone(third['nextCursor'])
        self.assertEqual(self.store.memory_inputs(limit=5), first['sources'] + second['sources'] + third['sources'])

    def test_memory_pages_bound_filtered_scans_and_advance_empty_pages(self):
        rows = [record('scan-' + str(i), 'Synthetic source ' + str(i), id='%064x' % (i + 1), project='other') for i in range(13)]
        rows[-1]['project'] = 'wanted'
        self.build(rows)
        self.store.assign_project(rows[1]['id'], 'wanted')
        self.store.exclude(rows[1]['id'])
        after, seen, empty_pages = None, [], 0
        with mock.patch.object(h, 'MEMORY_SCAN_LIMIT', 3):
            for _ in range(10):
                page = self.store.memory_input_page(project='wanted', limit=2, after=after)
                self.assertLessEqual(page['scanned'], 3)
                seen.extend(row['id'] for row in page['sources'])
                if page['exhausted']:
                    break
                self.assertIsNotNone(page['nextCursor'])
                self.assertNotEqual(page['nextCursor'], after)
                empty_pages += not page['sources']
                after = page['nextCursor']
            else:
                self.fail('Memory paging did not reach the end of its bounded scan.')
        self.assertGreater(empty_pages, 0)
        self.assertEqual(seen, [rows[-1]['id']])

    def test_memory_page_cursor_generation_validation_and_live_exclusions(self):
        self.assertEqual(self.store.memory_input_page(), dict(sources=[], nextCursor=None, generationId=None, exhausted=True,
                                                            scanned=0, selectionRevision=self.store.memory_selection_revision()))
        first, second = record('cursor-first', 'First source', id='1' * 64), record('cursor-second', 'Second source', id='2' * 64)
        self.build([first, second])
        page = self.store.memory_input_page(limit=1)
        cursor = page['nextCursor']
        self.store.exclude(first['id'])
        self.assertEqual([row['id'] for row in self.store.memory_input_page(after=cursor)['sources']], [second['id']])
        for invalid in ({}, dict(cursor, extra=True), dict(cursor, afterId='bad'), dict(cursor, generationId='bad'),
                        dict(cursor, afterId='0' * 64)):
            with self.assertRaises(ValueError):
                self.store.memory_input_page(after=invalid)
        self.build([record('cursor-second', 'A corrected source', id=second['id'])])
        with self.assertRaisesRegex(ValueError, 'different generation'):
            self.store.memory_input_page(after=cursor)
        replica = h.HistoryStore(self.root / 'replica', role='replica')
        try:
            with self.assertRaises(ValueError):
                replica.memory_input_page()
        finally:
            replica.close()

    def test_memory_selection_revision_changes_only_with_effective_selection(self):
        source = record('selection', 'Prefers source-backed notes.')
        self.build([source])
        original = self.store.memory_selection_revision()
        self.assertRegex(original, r'^[a-f0-9]{64}$')
        self.assertEqual(self.store.status()['memorySelectionRevision'], original)
        page = self.store.memory_input_page(project='fixture')
        self.assertTrue(page['exhausted'])
        self.assertEqual(page['selectionRevision'], original)
        proposal = self.store.propose_memory('Notes preference', [source['id']], request_id='b' * 32)
        self.store.review_memory(proposal['id'], 'edit', 'Reviewed preference')
        self.store.review_memory(proposal['id'], 'approve')
        self.assertEqual(self.store.memory_selection_revision(), original)
        self.store.exclude(source['id'])
        excluded = self.store.memory_selection_revision()
        self.assertNotEqual(excluded, original)
        self.assertEqual(self.store.memory_input_page()['selectionRevision'], excluded)
        self.store.exclude(source['id'])
        self.assertEqual(self.store.memory_selection_revision(), excluded)
        self.store.exclude(source['id'], False)
        self.assertEqual(self.store.memory_selection_revision(), original)
        self.store.assign_project(source['id'], 'different')
        retagged = self.store.memory_selection_revision()
        self.assertNotEqual(retagged, original)
        self.assertEqual(self.store.memory_input_page(project='fixture')['sources'], [])
        self.store.assign_project(source['id'], 'different')
        self.assertEqual(self.store.memory_selection_revision(), retagged)
        self.store.close(); self.store = h.HistoryStore(self.root / 'primary')
        self.assertEqual(self.store.status()['memorySelectionRevision'], retagged)
        self.assertEqual(self.store.memory_input_page(project='different')['sources'][0]['id'], source['id'])

    def test_exclusions_skip_generation_access_when_empty_or_fully_resolved(self):
        first, second = record('exclusion-a', 'First source'), record('exclusion-b', 'Second source')
        self.build([first, second])
        with mock.patch.object(self.store, '_view_generation', side_effect=AssertionError('Unexpected archive scan')):
            self.assertEqual(self.store.exclusions(), [])
        self.store.exclude(second['id'])
        self.store.exclude(first['id'])
        expected = self.store.exclusions()
        with mock.patch.object(self.store, '_view_generation', side_effect=AssertionError('Unexpected archive scan')):
            self.assertEqual(self.store.exclusions(), expected)
        self.assertEqual([row['value'] for row in expected], ['exclusion-a', 'exclusion-b'])
        self.assertEqual([row['sourceId'] for row in expected], [first['id'], second['id']])

    def test_exclusions_resolve_missing_hints_and_keep_explicit_generation_checks(self):
        source = record('missing-hint', 'Retained source')
        generation = self.build([source])['generationId']
        self.store.exclude(source['id'])
        expected = self.store.exclusions()
        with self.store._controls() as db:
            db.execute('DELETE FROM exclusion_sources')
        with mock.patch.object(self.store, '_view_generation', wraps=self.store._view_generation) as view:
            self.assertEqual(self.store.exclusions(), expected)
            self.assertGreater(view.call_count, 0)
        self.store.exclude(source['id'])
        expected = self.store.exclusions()
        with mock.patch.object(self.store, '_view_generation', wraps=self.store._view_generation) as view:
            self.assertEqual(self.store.exclusions(generation), expected)
            view.assert_called_once_with(generation)

    def test_restricted_lineage_overrides_accidental_true_and_future_messages(self):
        lineage = key('conversation:private')
        document = record('document', 'Private document', kind='document', memory_allowed=True,
                          metadata={'assetId': 'asset', 'conversationIds': ['private']})
        restricted = record('private', 'Do not remember', memory_allowed=True,
                            metadata={'conversationId': 'private', 'conversationSourceId': lineage},
                            restricted_source_ids=[lineage])
        self.build([document, restricted])
        for row in (document, restricted):
            source = self.store.source(row['id'])
            self.assertFalse(source['memory_allowed'])
            self.assertIn(lineage, source['metadata']['restricted_source_ids'])
            with self.assertRaises(ValueError):
                self.store.propose_memory('Must refuse', [row['id']])
        future = record('future', 'New accidentally eligible message', memory_allowed=True,
                        metadata={'conversationId': 'private'})
        self.build([future])
        self.assertFalse(self.store.source(future['id'])['memory_allowed'])
        self.assertIn(lineage, self.store.source(future['id'])['metadata']['restricted_source_ids'])
        metadatum = record('metadata-only', 'Restricted through metadata', memory_allowed=True,
                           metadata={'restricted_source_ids': [lineage]}, restricted_source_ids=[])
        self.build([metadatum])
        self.assertFalse(self.store.source(metadatum['id'])['memory_allowed'])
        new_segment = record('new-document-segment', 'New segment without old attachment associations', kind='document',
                             memory_allowed=True, metadata={'assetId': 'asset'})
        self.build([new_segment])
        self.assertFalse(self.store.source(new_segment['id'])['memory_allowed'])

    def test_prepared_generation_exposes_counts_without_text_or_receipt(self):
        first = self.store.build([record('a', 'PRIVATE_CANARY')], receipt={'secret': 'PRIVATE_CANARY'},
                                 coverage={'records': 1, 'issues': {'unsupported': 2}, 'path': 'PRIVATE_CANARY', 'rows': ['PRIVATE_CANARY']})
        info = self.store.generation_info(first['generationId'])
        self.assertEqual(info['coverage'], {'records': 1, 'issues': {'unsupported': 2}})
        self.assertEqual(len(self.store.prepared_generations()), 1)
        self.assertNotIn('PRIVATE_CANARY', json.dumps(self.store.status()))
        self.store.activate(first['generationId'])
        second = self.store.build([record('b', 'Second text')])
        self.assertEqual([row['generationId'] for row in self.store.prepared_generations()], [second['generationId']])
        self.store.activate(second['generationId'])
        self.assertEqual(self.store.prepared_generations(), [])

    def test_prepared_preview_exclusions_and_tags_do_not_activate(self):
        old = record('old', 'old active owl')
        active = self.build([old])
        new = record('new', 'prepared preview automobile', project='', metadata={'conversationId': 'new-conversation'})
        prepared = self.store.build([new])
        generation = prepared['generationId']
        self.assertEqual(self.store.search('automobile'), [])
        self.assertIsNone(self.store.source(new['id']))
        self.assertEqual(self.store.search('automobile', generation_id=generation)[0]['id'], new['id'])
        self.assertEqual(self.store.source(new['id'], generation_id=generation)['text'], new['text'])
        self.store.assign_project(new['id'], 'Prepared project', generation_id=generation)
        self.assertEqual(self.store.search('automobile', project='Prepared project', generation_id=generation)[0]['id'], new['id'])
        self.store.exclude(new['id'], generation_id=generation)
        self.assertEqual(self.store.search('automobile', generation_id=generation), [])
        self.assertIsNone(self.store.source(new['id'], generation_id=generation))
        self.assertEqual(self.store.exclusions()[0]['sourceId'], new['id'])
        self.assertEqual(self.store.status()['generation']['generationId'], active['generationId'])
        self.store.activate(generation)
        self.assertIsNone(self.store.source(new['id']))
        self.store.exclude(new['id'], False)
        self.assertEqual(self.store.source(new['id'])['project'], 'Prepared project')

    def test_prepared_preview_refuses_changed_index(self):
        prepared = self.store.build([record('new', 'prepared owl')])
        generation = prepared['generationId']
        self.assertTrue(self.store.search('owl', generation_id=generation))
        path = self.store.root / 'generations' / generation / 'index.sqlite3'
        with path.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'Prepared history index hash'):
            self.store.search('owl', generation_id=generation)
        self.assertFalse(self.store.status()['available'])

    def test_replica_prepared_exclusion_rules_are_immediate_without_text_import(self):
        source = record('prepared-only', 'PRIVATE_PREPARED_CANARY', metadata={'conversationId': 'prepared-conversation'})
        prepared = self.store.build([source])
        generation = prepared['generationId']
        replica = h.HistoryStore(self.root / 'replica', role='replica')
        fetched = self.store.source(source['id'], generation_id=generation)
        local = replica.exclude_record(fetched)
        self.assertEqual(local['ruleEntries'], [{'scope': 'conversation', 'value': 'prepared-conversation'}])
        self.assertFalse(replica.status()['available'])
        self.assertIsNone(replica.source(source['id']))
        self.assertEqual(replica.exclusions()[0]['sourceId'], source['id'])
        primary = self.store.exclude(source['id'], generation_id=generation)
        self.assertEqual(primary['ruleEntries'], local['ruleEntries'])
        self.assertIsNone(self.store.source(source['id'], generation_id=generation))
        # Restore resolves from the raw prepared index even when source() hides it.
        restored = self.store.exclude(source['id'], False, generation_id=generation)
        restarted = h.HistoryStore(replica.root, role='replica')
        restarted.apply_exclusion_rules(restored['ruleEntries'], False, source_id=source['id'])
        self.assertFalse(restarted.exclusions()[0]['enabled'])
        self.assertNotIn('PRIVATE_PREPARED_CANARY', json.dumps(restarted.exclusions()))
        self.assertNotIn('PRIVATE_PREPARED_CANARY', json.dumps(restarted.status()))

    def test_received_exclusion_rules_are_bounded_metadata_and_replica_only(self):
        replica = h.HistoryStore(self.root / 'replica', role='replica')
        valid = [{'scope': 'record', 'value': key('source')}]
        with self.assertRaises(ValueError):
            self.store.apply_exclusion_rules(valid)
        invalid = [[], valid * 101, [{'scope': 'unknown', 'value': 'x'}],
                   [{'scope': 'record', 'value': 'not-a-source-id'}],
                   [{'scope': 'conversation', 'value': 'x' * 501}],
                   [{'scope': 'conversation', 'value': 'x', 'text': 'PRIVATE_CANARY'}],
                   [{'scope': 'conversation', 'value': 'x\n'}], valid * 2]
        for rules in invalid:
            with self.assertRaises(ValueError):
                replica.apply_exclusion_rules(rules)
        with self.assertRaises(ValueError):
            replica.apply_exclusion_rules(valid, enabled='true')
        self.assertEqual(replica.exclusions(), [])

    def test_exclusion_rows_have_restorable_source_ids_without_content(self):
        a = record('a', 'PRIVATE_CANARY', metadata={'conversationId': 'shared'})
        document = record('doc', 'PRIVATE_CANARY', kind='document', metadata={'assetId': 'asset', 'conversationIds': ['shared']})
        self.build([document, a])
        self.store.exclude(a['id'])
        rule = self.store.exclusions()[0]
        self.assertEqual(rule['sourceId'], a['id'])
        self.assertNotIn('PRIVATE_CANARY', json.dumps(self.store.exclusions()))
        self.store.exclude(rule['sourceId'], False)
        self.assertIsNotNone(self.store.source(a['id']))
        self.store.exclude(document['id'])
        asset_rule = next(row for row in self.store.exclusions() if row['scope'] == 'asset')
        self.store.exclude(asset_rule['sourceId'], False)
        self.assertIsNotNone(self.store.source(document['id']))

    def test_project_assignment_is_durable_scoped_and_replicates(self):
        a = record('a', 'owl first', project='', metadata={'conversationId': 'shared'})
        b = record('b', 'owl second', project='', metadata={'conversationId': 'shared'})
        document = record('document', 'owl document', kind='document', project='', metadata={'assetId': 'asset', 'conversationIds': ['shared']})
        self.build([a, b, document])
        self.store.assign_project(a['id'], 'INTERLINKED')
        self.assertEqual(self.store.source(b['id'])['project'], 'INTERLINKED')
        self.assertEqual(len(self.store.search('owl', project='INTERLINKED')), 3)
        self.store.assign_project(document['id'], 'Media')
        self.assertEqual(self.store.source(document['id'])['project'], 'Media')
        self.assertEqual(len(self.store.search('owl', project='INTERLINKED')), 2)
        self.assertEqual(self.store.projects(), ['INTERLINKED', 'Media'])
        self.build([a, b, document])
        self.assertEqual(self.store.source(document['id'])['project'], 'Media')
        replica = h.HistoryStore(self.root / 'replica', role='replica')
        manifest = self.store.snapshot_manifest()
        replica.install_snapshot(manifest, lambda path, offset, length: self.store.snapshot_read_chunk(manifest['snapshotId'], path, offset, length))
        self.assertEqual(replica.source(b['id'])['project'], 'INTERLINKED')
        self.assertEqual(replica.search('owl', project='Media')[0]['id'], document['id'])
        with self.assertRaises(ValueError):
            replica.assign_project(a['id'], 'Not authority')

    def test_policy_revision_detects_same_count_memory_edits_and_project_moves(self):
        a, b, c = record('a', 'owl first', project=''), record('b', 'owl second', project=''), record('c', 'owl third', project='')
        self.build([a, b, c])
        proposal = self.store.propose_memory('original memory', [a['id']])
        self.store.review_memory(proposal['id'], 'approve')
        before = self.store.status()
        self.store.review_memory(proposal['id'], 'approve', 'edited approved memory')
        after = self.store.status()
        self.assertEqual(before['memories'], after['memories'])
        self.assertNotEqual(before['policyRevision'], after['policyRevision'])
        self.store.assign_project(a['id'], 'Project A')
        self.store.assign_project(b['id'], 'Project B')
        self.store.assign_project(c['id'], 'Project A')
        before = self.store.status()
        self.store.assign_project(a['id'], 'Project B')
        after = self.store.status()
        self.assertEqual(before['projects'], after['projects'])
        self.assertNotEqual(before['policyRevision'], after['policyRevision'])

    def test_snapshot_cache_reuses_immutable_payload_until_policy_changes(self):
        source = record('a', 'owl snapshot')
        self.build([source])
        manifest = self.store.snapshot_manifest()
        with mock.patch.object(h, 'file_hash', side_effect=AssertionError('Unchanged snapshots should not recopy or rehash payloads')):
            self.assertEqual(manifest, self.store.snapshot_manifest())
        self.store.assign_project(source['id'], 'A new project')
        changed = self.store.snapshot_manifest()
        self.assertNotEqual(manifest['snapshotId'], changed['snapshotId'])
        self.assertEqual(manifest['generationId'], changed['generationId'])

    def test_failed_and_cancelled_builds_preserve_active_generation(self):
        initial = self.build([record('one', 'owl initial')])
        event = threading.Event(); event.set()
        with self.assertRaises(InterruptedError):
            self.store.build([record('two', 'owl new')], cancel=event)
        with self.assertRaises(ValueError):
            self.store.build([record('one', 'conflict one'), record('one', 'conflict two')])
        self.assertEqual(self.store.status()['generation']['generationId'], initial['generationId'])
        self.assertEqual(self.store.search('initial')[0]['id'], key('one'))

    def test_keyword_filters_before_top_k_and_sql_is_data(self):
        values = [record(str(i), 'owl ' * 10, project='other') for i in range(50)]
        good = record('good', 'owl fixture', timestamp='2026-11-01T00:00:00Z')
        self.build(values + [good])
        found = self.store.search('owl', project='fixture', limit=1, date_from='2026-10-31', date_to='2026-11-01T23:59:59Z')
        self.assertEqual(found[0]['id'], good['id'])
        self.assertEqual(self.store.search('" OR * ; DROP TABLE records --'), [])
        self.assertEqual(len(self.store.search('owl', project='fixture')), 1)

    def test_snapshot_verified_install_keeps_local_tombstones(self):
        a, b = record('a', 'owl first'), record('b', 'owl second')
        self.build([a, b])
        proposal = self.store.propose_memory('owl first', [a['id']])
        self.store.review_memory(proposal['id'], 'approve')
        replica = h.HistoryStore(self.root / 'replica', role='replica')
        manifest = self.store.snapshot_manifest()
        reader = lambda path, offset, length: self.store.snapshot_read_chunk(manifest['snapshotId'], path, offset, length)
        replica.install_snapshot(manifest, reader)
        self.assertEqual(len(replica.memories('approved')), 1)
        replica.exclude(b['id'])
        self.store.exclude(a['id'])
        manifest = self.store.snapshot_manifest()
        reader = lambda path, offset, length: self.store.snapshot_read_chunk(manifest['snapshotId'], path, offset, length)
        replica.install_snapshot(manifest, reader)
        self.assertEqual(replica.search('owl'), [])
        self.assertEqual(replica.memories('approved'), [])
        for operation in (lambda: replica.build([]), lambda: replica.propose_memory('bad', [a['id']]),
                          lambda: replica.review_memory(proposal['id'], 'approve')):
            with self.assertRaises(ValueError):
                operation()

    def test_bad_snapshot_chunk_never_activates(self):
        self.build([record('a', 'owl first')])
        manifest = self.store.snapshot_manifest()
        replica = h.HistoryStore(self.root / 'replica', role='replica')
        def bad(path, offset, length):
            return b'x' * length
        with self.assertRaises(ValueError):
            replica.install_snapshot(manifest, bad)
        self.assertFalse(replica.status()['available'])
        malformed = copy.deepcopy(manifest)
        malformed['files'][0]['path'] = '../escape'
        base = dict(malformed); base.pop('snapshotId')
        malformed['snapshotId'] = hashlib.sha256(h.canonical(base)).hexdigest()
        with self.assertRaises(ValueError):
            replica.install_snapshot(malformed, bad)
        self.assertFalse((self.root / 'escape').exists())

    @unittest.skipIf(np is None, 'NumPy is a separately provisioned semantic runtime dependency')
    def test_semantic_paraphrase_blockwise_filters_and_identity_pin(self):
        semantic = h.HistoryStore(self.root / 'semantic', embedder=Embedder())
        good = record('car', 'car restoration')
        owl = record('owl', 'owl care')
        self.build([good, owl], semantic)
        self.assertEqual(semantic.search('automobile')[0]['id'], good['id'])
        semantic.exclude(good['id'])
        self.assertNotIn(good['id'], [row['id'] for row in semantic.search('automobile')])
        semantic.embedder.identity = dict(model='changed')
        with self.assertRaises(ValueError):
            semantic.search('automobile')

    def partial_vectors(self):
        active = self.build([record('baseline', 'owl baseline')])
        rows = [record('resume-' + str(i), ('car' if i % 2 else 'owl') + ' checkpoint ' + str(i)) for i in range(80)]
        embedder = CountingEmbedder()
        self.store.embedder = embedder
        event = threading.Event()
        def progress(value):
            if value['phase'] == 'embedding' and value['records'] >= 32:
                event.set()
        with self.assertRaises(InterruptedError):
            self.store.build(rows, cancel=event, progress=progress)
        checkpoints = list((self.store.root / 'generations').glob('*/BUILD.json'))
        partial = next(path for path in checkpoints if h.json_read(path)['phase'] == 'embedding')
        checkpoint = h.json_read(partial)
        self.assertEqual(checkpoint['rows'], 32)
        self.assertEqual(embedder.documents, 32)
        self.assertEqual(self.store.status()['generation']['generationId'], active['generationId'])
        return rows, active, partial, checkpoint

    @unittest.skipIf(np is None, 'NumPy is a separately provisioned semantic runtime dependency')
    def test_restart_resumes_only_verified_embedding_prefix_and_exact_offsets(self):
        rows, active, checkpoint_path, checkpoint = self.partial_vectors()
        vectors_path = checkpoint_path.parent / 'vectors.npy'
        matrix = np.load(vectors_path, mmap_mode='r+', allow_pickle=False)
        prefix = matrix[:32].copy()
        # A crash may have written rows beyond the last committed checkpoint.
        matrix[40] = [99, 99, 99]; matrix.flush(); del matrix
        embedder = CountingEmbedder()
        restarted = h.HistoryStore(self.store.root, embedder=embedder)
        result = restarted.build(rows)
        self.assertEqual(result['generationId'], checkpoint['generationId'])
        self.assertEqual(result['resumedVectors'], 32)
        self.assertEqual(embedder.documents, result['chunks'] - 32)
        self.assertEqual(restarted.status()['generation']['generationId'], active['generationId'])
        matrix = np.load(vectors_path, mmap_mode='r', allow_pickle=False)
        self.assertTrue(np.array_equal(matrix[:32], prefix))
        self.assertTrue(np.allclose(np.linalg.norm(matrix, axis=1), 1))
        del matrix
        restarted.activate(result['generationId'])
        hit = restarted.search('automobile')[0]
        self.assertIn('car', hit['excerpt'])
        self.assertEqual(restarted.source(hit['id'])['text'][hit['start']:hit['end']], hit['excerpt'])

    @unittest.skipIf(np is None, 'NumPy is a separately provisioned semantic runtime dependency')
    def test_corrupt_committed_vector_prefix_refuses_resume_without_activation(self):
        rows, active, checkpoint_path, checkpoint = self.partial_vectors()
        matrix = np.load(checkpoint_path.parent / 'vectors.npy', mmap_mode='r+', allow_pickle=False)
        matrix[0] = [42, 42, 42]; matrix.flush(); del matrix
        embedder = CountingEmbedder()
        restarted = h.HistoryStore(self.store.root, embedder=embedder)
        with self.assertRaisesRegex(ValueError, 'prefix hash'):
            restarted.build(rows)
        self.assertEqual(embedder.documents, 0)
        self.assertEqual(restarted.status()['generation']['generationId'], active['generationId'])

    @unittest.skipIf(np is None, 'NumPy is a separately provisioned semantic runtime dependency')
    def test_changed_input_does_not_reuse_an_unrelated_embedding_checkpoint(self):
        rows, active, checkpoint_path, checkpoint = self.partial_vectors()
        changed = list(rows)
        changed[0] = record('resume-0', 'car changed source content')
        embedder = CountingEmbedder()
        restarted = h.HistoryStore(self.store.root, embedder=embedder)
        result = restarted.build(changed)
        self.assertNotEqual(result['generationId'], checkpoint['generationId'])
        self.assertEqual(embedder.documents, result['chunks'])
        self.assertEqual(restarted.status()['generation']['generationId'], active['generationId'])

    @unittest.skipIf(np is None, 'NumPy is a separately provisioned semantic runtime dependency')
    def test_build_does_not_block_active_vectors_or_exclusions_and_freezes_embedder(self):
        self.store.embedder = Embedder()
        old = record('active-car', 'car previously verified')
        active = self.build([old])
        entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
        class GatedEmbedder(CountingEmbedder):
            def __call__(self, texts):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('Synthetic embedding gate was not released')
                return super().__call__(texts)
        build_embedder = GatedEmbedder()
        self.store.embedder = build_embedder
        result, errors = {}, []
        rows = [record('new-' + str(i), 'owl new record ' + str(i)) for i in range(40)]
        def build():
            try:
                result.update(self.store.build(rows))
            except Exception as exc:
                errors.append(exc)
        builder = threading.Thread(target=build)
        builder.start()
        self.assertTrue(entered.wait(5))
        # The service may replace a cancelled/background embedder with a fresh
        # interactive one. Subsequent build batches retain their original owner.
        self.store.embedder = Embedder()
        def read():
            try:
                self.assertEqual(self.store.search('automobile')[0]['id'], old['id'])
                self.store.exclude(old['id'])
                self.assertIsNone(self.store.source(old['id']))
            except Exception as exc:
                errors.append(exc)
            finally:
                read_done.set()
        reader = threading.Thread(target=read); reader.start()
        try:
            self.assertTrue(read_done.wait(3), 'Background embedding held the active read/control lock')
        finally:
            release.set()
            builder.join(10); reader.join(10)
        self.assertFalse(builder.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(build_embedder.documents, result['newVectors'])
        self.assertEqual(result['newVectors'] + result['reusedVectors'], result['chunks'])
        self.assertEqual(self.store.status()['generation']['generationId'], active['generationId'])


if __name__ == '__main__':
    unittest.main()
