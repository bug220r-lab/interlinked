"""Private local history generations, retrieval and reviewed memory.

This module never calls a cloud service or executes imported content.  Project
workspaces and the cloud-facing AI bridge deliberately do not import this store.
The caller owns model scheduling, archive intake and cross-machine transport.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import uuid

_spec = importlib.util.spec_from_file_location('history_configuration', Path(__file__).with_name('interlinked_configuration.py'))
_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_config)

FORMAT = 'interlinked-history-generation-v1'
SNAPSHOT = 'interlinked-history-snapshot-v1'
CHECKPOINT = 'interlinked-history-build-v1'
HEX = re.compile(r'^[a-f0-9]{64}$')
GEN = re.compile(r'^[a-f0-9]{32}$')
FILES = {'index.sqlite3', 'generation.json', 'vectors.npy', 'controls.json'}
MAX_CHUNK = 4 * 1024 * 1024
MAX_METADATA = 16 * 1024 * 1024
MAX_TEXT = 32 * 1024 * 1024
CHUNK_BYTES = 1400
OVERLAP_BYTES = 160
MEMORY_SCAN_LIMIT = 512
_UNSET = object()
VECTOR_FORMAT = dict(chunker='utf8-chars-v1', bytes=CHUNK_BYTES, overlap=OVERLAP_BYTES,
                     normalization='l2-float32-v1')
REUSE_POLICIES = {'sealed-vector-format-v1', 'reviewed-r4-vector-format-v1'}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def need(value, message):
    if not value:
        raise ValueError(message)


def cancelled(cancel):
    if cancel is not None and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel()):
        raise InterruptedError('History operation cancelled; the active generation is unchanged.')


def file_hash(path):
    _config.ordinary(path)
    result = hashlib.sha256()
    with open(path, 'rb') as stream:
        before = os.fstat(stream.fileno())
        while data := stream.read(1024 * 1024):
            result.update(data)
        after = os.fstat(stream.fileno())
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'History file changed during verification.')
    _config.ordinary(path)
    return result.hexdigest()


def json_read(path):
    return json.loads(_config.read_bytes(path, MAX_METADATA))


def json_write(path, value):
    _config.atomic(path, canonical(value), MAX_METADATA)


def normalized_record(value):
    need(isinstance(value, dict), 'History record must be an object.')
    need(isinstance(value.get('id'), str) and HEX.fullmatch(value['id']), 'Invalid history source ID.')
    need(isinstance(value.get('revision'), str) and HEX.fullmatch(value['revision']), 'Invalid history revision.')
    out = dict(value)
    for key, maximum in (('kind', 80), ('title', 4000), ('project', 200), ('text', MAX_TEXT)):
        out.setdefault(key, '')
        need(isinstance(out[key], str) and '\0' not in out[key] and len(out[key].encode('utf-8')) <= maximum,
             'Invalid or oversized history ' + key + '.')
    out.setdefault('timestamp', None)
    need(out['timestamp'] is None or isinstance(out['timestamp'], str) and len(out['timestamp']) <= 100,
         'Invalid history timestamp.')
    if out['timestamp']:
        try:
            parsed = dt.datetime.fromisoformat(out['timestamp'].replace('Z', '+00:00'))
            need(parsed.tzinfo is not None, 'History timestamps must include a timezone.')
            out['timestamp'] = parsed.astimezone(dt.timezone.utc).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid history timestamp.') from exc
    out.setdefault('memory_allowed', False)
    need(type(out['memory_allowed']) is bool, 'Memory eligibility must be explicit.')
    out.setdefault('metadata', {})
    need(isinstance(out['metadata'], dict) and len(canonical(out['metadata'])) <= 128 * 1024, 'Invalid history metadata.')
    out['metadata'] = dict(out['metadata'])
    inherited = out['metadata'].get('restricted_source_ids', [])
    declared = value.get('restricted_source_ids', [])
    need(all(isinstance(ids, list) and len(ids) <= 10000 and all(isinstance(key, str) and HEX.fullmatch(key) for key in ids)
             for ids in (inherited, declared)),
         'Invalid history restriction lineage.')
    restricted = inherited + declared
    out['metadata']['restricted_source_ids'] = sorted(set(restricted))
    if restricted or out['metadata'].get('doNotRemember') or out['metadata'].get('do_not_remember') or out['kind'].lower().startswith('group'):
        out['memory_allowed'] = False
    return {key: out[key] for key in ('id', 'revision', 'kind', 'title', 'text', 'timestamp', 'project', 'memory_allowed', 'metadata')}


def chunks(text):
    """Bound UTF-8 input while retaining exact, reproducible character offsets."""
    start = 0
    while start < len(text):
        piece = text[start:start + CHUNK_BYTES].encode('utf-8')[:CHUNK_BYTES].decode('utf-8', 'ignore')
        end = start + len(piece)
        if end < len(text):
            boundary = max(text.rfind('\n', start, end), text.rfind(' ', start, end))
            if boundary > start + len(piece) // 2:
                end = boundary + 1
        yield start, end, text[start:end]
        if end >= len(text):
            break
        overlap = text[max(start, end - OVERLAP_BYTES):end].encode('utf-8')[-OVERLAP_BYTES:].decode('utf-8', 'ignore')
        start = max(start + 1, end - len(overlap))


class HistoryStore:
    def __init__(self, root, role='primary', embedder=None):
        need(role in ('primary', 'replica'), 'Choose primary or replica history role.')
        self.root = _config.ordinary(Path(root).absolute(), missing=True)
        self.role, self.embedder = role, embedder
        self.lock = threading.RLock()
        self._writer_locks = {name: threading.RLock() for name in ('state', 'build', 'export', 'sync')}
        self._verified_views = {}
        self.root.mkdir(parents=True, exist_ok=True)
        _config.ordinary(self.root)
        owner = self.root / 'OWNER.json'
        expected = dict(format='interlinked-history-owner-v1', root=str(self.root), role=role)
        if owner.exists():
            need(json_read(owner) == expected, 'History ownership or machine role differs.')
        else:
            need(not any(self.root.iterdir()), 'History state directory is not empty or owned.')
            json_write(owner, expected)
        for name in ('generations', 'exports'):
            path = _config.ordinary(self.root / name, missing=True)
            path.mkdir(exist_ok=True)
        self.controls_path = self.root / 'controls.sqlite3'
        _config.ordinary(self.controls_path, missing=True)
        with self._controls() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS exclusions(scope TEXT, value TEXT, origin TEXT, enabled INTEGER,
                  updated TEXT, PRIMARY KEY(scope,value,origin));
                CREATE TABLE IF NOT EXISTS exclusion_sources(scope TEXT,value TEXT,source_id TEXT,
                  PRIMARY KEY(scope,value));
                CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, text TEXT, category TEXT, project TEXT,
                  status TEXT, refs TEXT, created TEXT, updated TEXT);
                CREATE TABLE IF NOT EXISTS memory_requests(request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                  memory_id TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS project_assignments(scope TEXT,value TEXT,project TEXT,updated TEXT,
                  PRIMARY KEY(scope,value));
                CREATE TABLE IF NOT EXISTS activations(generation_id TEXT PRIMARY KEY,activated TEXT);
                CREATE TABLE IF NOT EXISTS exclusion_meta(id INTEGER PRIMARY KEY CHECK(id=1),replica_id TEXT NOT NULL,
                  ordinal INTEGER NOT NULL,migrated INTEGER NOT NULL DEFAULT 0,policy_revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS exclusion_versions(scope TEXT,value TEXT,ordinal INTEGER NOT NULL,
                  PRIMARY KEY(scope,value));
                CREATE TABLE IF NOT EXISTS exclusion_outbox(scope TEXT,value TEXT,ordinal INTEGER NOT NULL,
                  fingerprint TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scope,value));
                CREATE TABLE IF NOT EXISTS exclusion_unresolved(id TEXT PRIMARY KEY,payload TEXT NOT NULL,reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS exclusion_receipts(replica_id TEXT,scope TEXT,value TEXT,ordinal INTEGER NOT NULL,
                  fingerprint TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(replica_id,scope,value));
                CREATE TABLE IF NOT EXISTS exclusion_pairings(caller TEXT PRIMARY KEY,replica_id TEXT NOT NULL);
            ''')
            if 'policy_revision' not in {r[1] for r in db.execute('PRAGMA table_info(exclusion_meta)')}:
                db.execute('ALTER TABLE exclusion_meta ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT 0')
            db.execute('INSERT OR IGNORE INTO exclusion_meta(id,replica_id,ordinal,migrated,policy_revision) VALUES(1,?,0,0,0)', (uuid.uuid4().hex,))
        if role == 'replica':
            self._recover_sync_stages()

    @contextmanager
    def _controls(self):
        _config.ordinary(self.controls_path, missing=True)
        db = sqlite3.connect(self.controls_path)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _writer(self, purpose='state'):
        """One writer across local processes; a crashed owner leaves no held lock."""
        need(purpose in self._writer_locks, 'Unknown history write lease.')
        with self._writer_locks[purpose]:
            path = _config.ordinary(self.root / ('writer.lock' if purpose == 'state' else purpose + '.lock'), missing=True)
            with open(path, 'a+b') as stream:
                if stream.tell() == 0:
                    stream.write(b'0'); stream.flush()
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        raise ValueError('Another history writer owns this store.') from exc
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _primary(self):
        need(self.role == 'primary', 'Only the authoritative history store may import or review memories.')

    def _generation(self, generation_id=None):
        if generation_id is None:
            active = self.root / 'ACTIVE.json'
            if not active.exists():
                return None
            pointer = json_read(active)
            generation_id = pointer.get('generationId')
        need(isinstance(generation_id, str) and GEN.fullmatch(generation_id), 'Invalid history generation ID.')
        path = _config.ordinary(self.root / 'generations' / generation_id)
        need(path.is_dir(), 'History generation is unavailable.')
        info = json_read(path / 'generation.json')
        need(info.get('format') == FORMAT and info.get('generationId') == generation_id and info.get('complete') is True,
             'History generation is incomplete.')
        return path, info

    def _view_generation(self, generation_id=None):
        generation = self._generation(generation_id)
        if generation_id is None or generation is None:
            return generation
        path, info = generation
        names = ['index.sqlite3', 'generation.json'] + (['vectors.npy'] if info.get('semantic') else [])
        stamps = []
        for name in names:
            state = os.stat(_config.ordinary(path / name))
            stamps.append((name, state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns))
        proof = (info.get('indexSha256'), info.get('vectorsSha256'), tuple(stamps))
        if self._verified_views.get(generation_id) != proof:
            need(file_hash(path / 'index.sqlite3') == info['indexSha256'], 'Prepared history index hash differs.')
            if info.get('semantic'):
                need(file_hash(path / 'vectors.npy') == info['vectorsSha256'], 'Prepared history vectors hash differs.')
            self._verified_views[generation_id] = proof
        return generation

    @contextmanager
    def _index(self, generation=None):
        generation = generation or self._generation()
        need(generation is not None, 'No history generation is active.')
        path = _config.ordinary(generation[0] / 'index.sqlite3')
        db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            yield db
        finally:
            db.close()

    def _embedding_identity(self, embedder=_UNSET):
        embedder = self.embedder if embedder is _UNSET else embedder
        identity = getattr(embedder, 'identity', None)
        if embedder is not None:
            need(isinstance(identity, dict) and isinstance(identity.get('model'), str) and
                 isinstance(identity.get('digest'), str) and HEX.fullmatch(identity['digest']),
                 'Local history embeddings require a pinned model identity.')
        return json.loads(canonical(identity)) if identity is not None else None

    @staticmethod
    def _schema(db):
        db.executescript('''
            CREATE TABLE IF NOT EXISTS records(id TEXT, revision TEXT, kind TEXT, title TEXT, text TEXT,
              timestamp TEXT, project TEXT, memory_allowed INTEGER, metadata TEXT,
              PRIMARY KEY(id,revision));
            CREATE TABLE IF NOT EXISTS heads(id TEXT PRIMARY KEY, revision TEXT, seen INTEGER);
            CREATE TABLE IF NOT EXISTS vector_ids(position INTEGER PRIMARY KEY,id TEXT,revision TEXT);
            CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,source_id TEXT,revision TEXT,start INTEGER,end INTEGER,text TEXT);
            CREATE TABLE IF NOT EXISTS restrictions(id TEXT PRIMARY KEY);
            CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(id UNINDEXED,revision UNINDEXED,title,text);
        ''')

    def _vector_donor(self, proof, identity, cancel=None):
        """Explicit immutable vector donor; never a record-retention/activation base.

        The R4 policy is an explicit operator attestation for the historical
        utf8-chars-v1/1400/160 and l2-float32-v1 processor. R4 omitted vectorFormat.
        Its reviewed chunker and embedding-prefix code match this format;
        absence of a marker alone never grants compatibility.
        """
        need(isinstance(proof, dict) and set(proof) == {'generationId', 'metadataSha256', 'indexSha256', 'vectorsSha256', 'processingPolicy'},
             'An exact vector reuse proof is required.')
        need(isinstance(proof['processingPolicy'], str) and proof['processingPolicy'] in REUSE_POLICIES and
             all(isinstance(proof[k], str) and HEX.fullmatch(proof[k]) for k in ('metadataSha256', 'indexSha256', 'vectorsSha256')),
             'Invalid vector reuse proof.')
        path, info = self._generation(proof['generationId'])
        for filename, key in [('generation.json', 'metadataSha256'), ('index.sqlite3', 'indexSha256'), ('vectors.npy', 'vectorsSha256')]:
            cancelled(cancel)
            need(file_hash(_config.ordinary(path / filename)) == proof[key], 'Vector donor bytes differ from the reviewed proof.')
        need(info.get('semantic') is True and info.get('embeddingIdentity') == identity and
             info.get('indexSha256') == proof['indexSha256'] and info.get('vectorsSha256') == proof['vectorsSha256'],
             'Vector donor embedding identity or seal differs.')
        format_ok = info.get('vectorFormat') == VECTOR_FORMAT
        if proof['processingPolicy'] == 'reviewed-r4-vector-format-v1':
            format_ok = 'vectorFormat' not in info
        need(format_ok, 'Vector donor processing format differs; explicit reviewed legacy compatibility is required.')
        import numpy as np
        values = np.load(_config.ordinary(path / 'vectors.npy'), mmap_mode='r', allow_pickle=False)
        try:
            need(type(info.get('chunks')) is int and info['chunks'] > 0 and type(info.get('dimensions')) is int and
                 1 <= info['dimensions'] <= 8192 and values.shape == (info['chunks'], info['dimensions']) and
                 str(values.dtype) == 'float32' and values.flags.c_contiguous and
                 (path / 'vectors.npy').stat().st_size == values.offset + values.nbytes, 'Vector donor shape differs.')
            for start in range(0, len(values), 2048):
                cancelled(cancel)
                rows = np.asarray(values[start:start + 2048])
                need(np.isfinite(rows).all() and np.all(np.abs(np.linalg.norm(rows, axis=1) - 1) < 1e-4),
                     'Vector donor contains invalid or unnormalized rows.')
        finally:
            del values
        with closing(sqlite3.connect((path / 'index.sqlite3').as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            need(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Vector donor index is invalid.')
            size, low, high, unique_positions, integer_positions = db.execute(
                "SELECT count(*),min(position),max(position),count(distinct position),"
                "sum(typeof(position)='integer') FROM vector_ids").fetchone()
            need(size == info['chunks'] and low == 0 and high == size - 1 and
                 unique_positions == size and integer_positions == size and
                 db.execute('SELECT count(*) FROM chunks').fetchone()[0] == size and
                 db.execute('SELECT count(*) FROM (SELECT id,revision FROM chunks GROUP BY id,revision)').fetchone()[0] == size and
                 db.execute('SELECT count(*) FROM (SELECT id,revision FROM vector_ids GROUP BY id,revision)').fetchone()[0] == size and
                 db.execute('SELECT count(*) FROM vector_ids v JOIN chunks c ON c.id=v.id AND c.revision=v.revision').fetchone()[0] == size,
                 'Vector donor position map differs.')
        return path, dict(info, vectorFormat=dict(VECTOR_FORMAT))

    def build(self, records, receipt=None, coverage=None, cancel=None, progress=None, reuse_generation=None):
        self._primary()
        generation_id = uuid.uuid4().hex
        build_embedder = self.embedder
        embedding_identity = self._embedding_identity(build_embedder)
        with self._writer('build'):
            cancelled(cancel)
            donor = self._vector_donor(reuse_generation, embedding_identity, cancel) if reuse_generation is not None else None
            path = self.root / 'generations' / generation_id
            path.mkdir()
            _config.ordinary(path)
            previous = self._generation()
            if previous:
                shutil.copyfile(previous[0] / 'index.sqlite3', path / 'index.sqlite3')
            db = sqlite3.connect(path / 'index.sqlite3')
            db.row_factory = sqlite3.Row
            try:
                self._schema(db)
                db.execute('UPDATE heads SET seen=0')
                count, seen, inputs = 0, {}, hashlib.sha256()
                for raw in records:
                    cancelled(cancel)
                    record = normalized_record(raw)
                    inputs.update(canonical(record)); inputs.update(b'\n')
                    key, revision = record['id'], record['revision']
                    need(key not in seen or seen[key] == revision, 'One import contains conflicting revisions of a source.')
                    seen[key] = revision
                    prior = db.execute('SELECT * FROM records WHERE id=? AND revision=?', (key, revision)).fetchone()
                    if prior:
                        need(prior['text'] == record['text'], 'A source revision was reused for different content.')
                    # A restricted source must never regain automatic memory eligibility
                    # merely because a later export omits its restriction metadata.
                    restricted = db.execute('SELECT 1 FROM records WHERE id=? AND memory_allowed=0 LIMIT 1', (key,)).fetchone()
                    record['memory_allowed'] = record['memory_allowed'] and not bool(restricted)
                    policies = set(record['metadata']['restricted_source_ids'])
                    if policies:
                        policies.update(hashlib.sha256(('asset:' + value).encode('utf-8')).hexdigest()
                                        for scope, value in self._lineage(record) if scope == 'asset')
                    if record['metadata'].get('doNotRemember') or record['metadata'].get('do_not_remember') or record['kind'].lower().startswith('group'):
                        policies.update(self._restriction_lineage(record))
                    db.executemany('INSERT OR IGNORE INTO restrictions VALUES(?)', [(policy,) for policy in policies])
                    db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?,?,?)',
                               (key, revision, record['kind'], record['title'], record['text'], record['timestamp'],
                                record['project'], int(record['memory_allowed']), canonical(record['metadata']).decode()))
                    db.execute('INSERT OR REPLACE INTO heads VALUES(?,?,1)', (key, revision))
                    count += 1
                    if progress and count % 100 == 0:
                        progress(dict(phase='normalizing', records=count))
                # A restriction encountered later in the iterator also covers earlier
                # records, old revisions and newly exported messages of that source.
                policies = {row[0] for row in db.execute('SELECT id FROM restrictions')}
                policy_rows = db.execute('SELECT id,revision,kind,memory_allowed,metadata FROM records').fetchall()
                for row in policy_rows:
                    record = self._row(row)
                    if policies.intersection(self._restriction_lineage(record)):
                        policies.update(hashlib.sha256(('asset:' + value).encode('utf-8')).hexdigest()
                                        for scope, value in self._lineage(record) if scope == 'asset')
                db.executemany('INSERT OR IGNORE INTO restrictions VALUES(?)', [(policy,) for policy in policies])
                for row in policy_rows:
                    record = self._row(row)
                    inherited = policies.intersection(self._restriction_lineage(record))
                    if inherited:
                        record['metadata']['restricted_source_ids'] = sorted(set(record['metadata'].get('restricted_source_ids', [])) | inherited)
                        db.execute('UPDATE records SET memory_allowed=0,metadata=? WHERE id=? AND revision=?',
                                   (canonical(record['metadata']).decode(), record['id'], record['revision']))
                db.execute('DELETE FROM fts')
                db.execute('DELETE FROM vector_ids')
                db.execute('DELETE FROM chunks')
                source_rows = db.execute('SELECT r.id,r.revision,r.title,r.text FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE length(r.text)>0')
                for source in source_rows:
                    cancelled(cancel)
                    for start, end, text in chunks(source['text']):
                        chunk_id = hashlib.sha256(canonical([source['id'], source['revision'], start, end])).hexdigest()
                        db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)', (chunk_id, source['id'], source['revision'], start, end, text))
                        db.execute('INSERT INTO fts VALUES(?,?,?,?)', (chunk_id, source['revision'], source['title'], text))
                # Position mapping is immutable before any embedding request. A
                # checkpoint can therefore bind one DB hash for the entire build.
                for position, row in enumerate(db.execute('SELECT id,revision FROM chunks ORDER BY id')):
                    db.execute('INSERT INTO vector_ids VALUES(?,?,?)', (position, row['id'], row['revision']))
                db.commit()
                info = dict(format=FORMAT, generationId=generation_id, createdAt=utc(), complete=False,
                            inputRecords=count, records=db.execute('SELECT count(*) FROM heads').fetchone()[0],
                            revisions=db.execute('SELECT count(*) FROM records').fetchone()[0],
                            chunks=db.execute('SELECT count(*) FROM chunks').fetchone()[0],
                            retainedAbsent=db.execute('SELECT count(*) FROM heads WHERE seen=0').fetchone()[0],
                            receipt=receipt or {}, coverage=coverage or {}, semantic=False,
                            embeddingIdentity=embedding_identity, vectorFormat=VECTOR_FORMAT,
                            reusedVectors=0, newVectors=0)
                if donor is not None:
                    info['vectorReuseProof'] = dict(reuse_generation)
                build_key = hashlib.sha256(canonical(dict(inputs=inputs.hexdigest(), base=previous[1]['generationId'] if previous else None,
                    embedding=info['embeddingIdentity'], chunker=dict(format=CHECKPOINT, bytes=CHUNK_BYTES, overlap=OVERLAP_BYTES),
                    **({'vectorReuseProof': reuse_generation} if reuse_generation is not None else {})))).hexdigest()
                checkpoint = self._resume_checkpoint(build_key, generation_id)
                if checkpoint:
                    candidate = path
                    db.close(); db = None
                    generation_id = checkpoint['generationId']
                    path = _config.ordinary(self.root / 'generations' / generation_id)
                    info = checkpoint['info']
                    info['resumedVectors'] = checkpoint['rows']
                    db = sqlite3.connect(path / 'index.sqlite3')
                    db.row_factory = sqlite3.Row
                    # Only this invocation's redundant normalization candidate is
                    # removed; other generations and partial builds are retained.
                    _config.ordinary(candidate / 'index.sqlite3').unlink()
                    candidate.rmdir()
                    if progress:
                        progress(dict(phase='resuming', records=checkpoint['rows'], total=info['chunks'], generationId=generation_id))
                    if checkpoint['phase'] == 'ready':
                        finished = self._generation(generation_id)
                        need(file_hash(path / 'index.sqlite3') == finished[1]['indexSha256'], 'History checkpoint completed index differs.')
                        if finished[1].get('semantic'):
                            need(file_hash(path / 'vectors.npy') == finished[1]['vectorsSha256'], 'History checkpoint completed vectors differ.')
                        return self._safe_info(info)
                else:
                    checkpoint = dict(format=CHECKPOINT, key=build_key, generationId=generation_id,
                        phase='normalized', indexSha256=file_hash(path / 'index.sqlite3'), rows=0,
                        prefixSha256=hashlib.sha256(b'').hexdigest(), total=info['chunks'], dimensions=None, info=info)
                    json_write(path / 'BUILD.json', checkpoint)
                if build_embedder is not None:
                    self._build_vectors(db, path, info, cancel, progress, checkpoint, build_embedder, donor or previous)
                if donor is not None:
                    for filename, key in [('generation.json', 'metadataSha256'), ('index.sqlite3', 'indexSha256'), ('vectors.npy', 'vectorsSha256')]:
                        need(file_hash(_config.ordinary(donor[0] / filename)) == reuse_generation[key], 'Vector donor changed during build.')
                cancelled(cancel)
                db.commit()
                need(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'History index verification failed.')
                db.close(); db = None
                info['complete'] = True
                info['indexSha256'] = file_hash(path / 'index.sqlite3')
                if (path / 'vectors.npy').exists():
                    info['vectorsSha256'] = file_hash(path / 'vectors.npy')
                json_write(path / 'generation.json', info)
                checkpoint.update(phase='ready', info=info)
                json_write(path / 'BUILD.json', checkpoint)
                if progress:
                    progress(dict(phase='ready', records=info['records'], generationId=generation_id))
                return self._safe_info(info)
            finally:
                if db is not None:
                    db.close()

    def _resume_checkpoint(self, build_key, candidate_id):
        matches = []
        for path in _config.ordinary(self.root / 'generations').iterdir():
            if not GEN.fullmatch(path.name) or path.name == candidate_id:
                continue
            checkpoint_path = _config.ordinary(path / 'BUILD.json', missing=True)
            if not checkpoint_path.exists():
                continue
            value = json_read(checkpoint_path)
            if value.get('key') == build_key:
                need(value.get('format') == CHECKPOINT and value.get('generationId') == path.name and
                     value.get('phase') in ('normalized', 'embedding', 'ready') and isinstance(value.get('info'), dict) and
                     value['info'].get('generationId') == path.name and type(value.get('rows')) is int and
                     type(value.get('total')) is int and 0 <= value['rows'] <= value['total'] and
                     isinstance(value.get('prefixSha256'), str) and HEX.fullmatch(value['prefixSha256']),
                     'History build checkpoint requires review.')
                need(value['info'].get('chunks') == value['total'] and (value['rows'] > 0 or value['prefixSha256'] == hashlib.sha256(b'').hexdigest()),
                     'History checkpoint offsets require review.')
                need(file_hash(path / 'index.sqlite3') == value.get('indexSha256'), 'History checkpoint index hash differs.')
                matches.append(value)
        return max(matches, key=lambda value: (value['rows'], value['generationId'])) if matches else None

    def _build_vectors(self, db, path, info, cancel, progress, checkpoint, embedder, previous=None):
        try:
            import numpy as np
        except ImportError as exc:
            raise ValueError('NumPy is required for local semantic history search.') from exc
        total = db.execute('SELECT count(*) FROM chunks').fetchone()[0]
        if not total:
            return
        need(checkpoint['total'] == total and db.execute('SELECT count(*) FROM vector_ids').fetchone()[0] == total,
             'History checkpoint vector offsets differ.')
        matrix, old_matrix, old_db, digest = None, None, None, hashlib.sha256()
        position = checkpoint['rows']
        vector_path = _config.ordinary(path / 'vectors.npy', missing=True)
        try:
            if previous and previous[1].get('semantic') and previous[1].get('embeddingIdentity') == info['embeddingIdentity'] and previous[1].get('vectorFormat') == VECTOR_FORMAT:
                old_path, old_info = previous
                # Corrupt or incompatible prior data is never reused; rebuilding is safe.
                if file_hash(old_path / 'index.sqlite3') == old_info.get('indexSha256') and file_hash(old_path / 'vectors.npy') == old_info.get('vectorsSha256'):
                    candidate = np.load(_config.ordinary(old_path / 'vectors.npy'), mmap_mode='r', allow_pickle=False)
                    if candidate.ndim == 2 and candidate.shape == (old_info['chunks'],old_info['dimensions']) and str(candidate.dtype) == 'float32' and candidate.flags.c_contiguous:
                        old_matrix = candidate
                        old_db = sqlite3.connect((old_path / 'index.sqlite3').as_uri() + '?mode=ro&immutable=1',uri=True)
                        old_db.execute('PRAGMA trusted_schema=OFF')
                        # Historical donors have no id/revision index. Keep the
                        # donor immutable and bound the lookup cache on disk.
                        old_db.execute('PRAGMA temp_store=FILE')
                        old_db.execute('PRAGMA temp.cache_size=-2048')
                        old_db.execute('CREATE TEMP TABLE vector_lookup(id TEXT NOT NULL,revision TEXT NOT NULL,position INTEGER NOT NULL,PRIMARY KEY(id,revision)) WITHOUT ROWID')
                        positions = old_db.execute('SELECT id,revision,position FROM vector_ids')
                        while batch := positions.fetchmany(2048):
                            cancelled(cancel)
                            old_db.executemany('INSERT INTO temp.vector_lookup VALUES(?,?,?)', batch)
                    else:
                        del candidate
            if position:
                matrix = np.load(_config.ordinary(vector_path), mmap_mode='r+', allow_pickle=False)
                need(type(checkpoint.get('dimensions')) is int and matrix.shape == (total, checkpoint['dimensions']) and
                     str(matrix.dtype) == 'float32' and matrix.flags.c_contiguous and vector_path.stat().st_size == matrix.offset + matrix.nbytes,
                     'History checkpoint vector shape differs.')
                for start in range(0, position, 2048):
                    cancelled(cancel)
                    digest.update(np.asarray(matrix[start:min(position, start + 2048)]).tobytes(order='C'))
                need(digest.hexdigest() == checkpoint['prefixSha256'], 'History checkpoint vector prefix hash differs.')
                info['dimensions'] = checkpoint['dimensions']
            cursor = db.execute('SELECT c.id,c.revision,c.text FROM vector_ids v JOIN chunks c ON v.id=c.id AND v.revision=c.revision WHERE v.position>=? ORDER BY v.position', (position,))
            while rows := cursor.fetchmany(32):
                cancelled(cancel)
                reused, missing = {}, []
                for i,row in enumerate(rows):
                    prior = old_db.execute('SELECT position FROM temp.vector_lookup WHERE id=? AND revision=?',(row['id'],row['revision'])).fetchone() if old_db else None
                    if prior and 0 <= prior[0] < len(old_matrix):
                        vector = np.asarray(old_matrix[prior[0]])
                        need(np.isfinite(vector).all() and abs(float(np.linalg.norm(vector))-1) < .001, 'Prior normalized embedding differs.')
                        reused[i] = vector
                    else:
                        missing.append(i)
                generated = np.asarray(embedder([rows[i]['text'] for i in missing]),dtype=np.float32) if missing else None
                if generated is not None:
                    need(generated.ndim == 2 and generated.shape[0] == len(missing) and np.isfinite(generated).all(), 'Invalid local embedding batch.')
                    lengths = np.linalg.norm(generated,axis=1)
                    need((lengths > 0).all(), 'Zero-length local embedding.')
                    generated = generated / lengths[:,None]
                dimensions = generated.shape[1] if generated is not None else old_matrix.shape[1]
                need(not reused or dimensions == old_matrix.shape[1], 'Embedding dimensions changed across generations.')
                values = np.empty((len(rows),dimensions),dtype=np.float32)
                for i,vector in reused.items(): values[i] = vector
                for i,j in enumerate(missing): values[j] = generated[i]
                need(values.ndim == 2 and values.shape[0] == len(rows) and 1 <= values.shape[1] <= 8192 and np.isfinite(values).all(),
                     'Invalid local embedding batch.')
                identity = info.get('embeddingIdentity') or {}
                need(not identity.get('dimensions') or identity['dimensions'] == values.shape[1], 'Embedding dimensions differ from the pinned model.')
                if matrix is None:
                    matrix = np.lib.format.open_memmap(path / 'vectors.npy', mode='w+', dtype=np.float32, shape=(total, values.shape[1]))
                    info['dimensions'] = int(values.shape[1])
                need(values.shape[1] == matrix.shape[1], 'Embedding dimensions changed during indexing.')
                matrix[position:position + len(rows)] = values
                matrix.flush()
                with open(vector_path, 'r+b') as stream:
                    os.fsync(stream.fileno())
                digest.update(np.asarray(matrix[position:position + len(rows)]).tobytes(order='C'))
                position += len(rows)
                info['reusedVectors'] = info.get('reusedVectors',0) + len(reused)
                info['newVectors'] = info.get('newVectors',0) + len(missing)
                checkpoint.update(phase='embedding', rows=position, dimensions=info['dimensions'],
                                  prefixSha256=digest.hexdigest(), info=info)
                json_write(path / 'BUILD.json', checkpoint)
                if progress:
                    progress(dict(phase='embedding', records=position, total=total))
            matrix.flush()
            need(position == total, 'History embedding offsets are incomplete.')
            info['semantic'] = True
        finally:
            if matrix is not None:
                del matrix
            if old_matrix is not None:
                del old_matrix
            if old_db is not None:
                old_db.close()

    def activate(self, generation_id):
        with self._writer():
            path, info = self._generation(generation_id)
            need(file_hash(path / 'index.sqlite3') == info['indexSha256'], 'History index hash differs.')
            if info.get('semantic'):
                need(file_hash(path / 'vectors.npy') == info['vectorsSha256'], 'History vectors hash differs.')
            json_write(self.root / 'ACTIVE.json', dict(generationId=generation_id, activatedAt=utc()))
            with self._controls() as db:
                db.execute('INSERT OR REPLACE INTO activations VALUES(?,?)', (generation_id, utc()))
            return self.status()

    @staticmethod
    def _safe_info(info):
        out = {key: info[key] for key in ('generationId', 'createdAt', 'inputRecords', 'records', 'revisions', 'chunks',
                                         'retainedAbsent', 'semantic', 'dimensions', 'embeddingIdentity', 'resumedVectors', 'reusedVectors', 'newVectors') if key in info}
        out['coverage'] = HistoryStore._count_coverage(info.get('coverage', {}))
        return out

    @staticmethod
    def _count_coverage(value):
        if not isinstance(value, dict):
            return {}
        out = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 100:
                continue
            if isinstance(item, dict):
                out[key] = HistoryStore._count_coverage(item)
            elif type(item) in (int, float, bool) and (not isinstance(item, float) or math.isfinite(item)):
                out[key] = item
        return out

    def generation_info(self, generation_id=None):
        with self.lock:
            generation = self._generation(generation_id)
            return self._safe_info(generation[1]) if generation else None

    def prepared_generations(self):
        with self.lock:
            active = self._generation()
            active_id = active[1]['generationId'] if active else None
            with self._controls() as db:
                activated = {row[0] for row in db.execute('SELECT generation_id FROM activations')}
            rows = []
            for path in _config.ordinary(self.root / 'generations').iterdir():
                if not GEN.fullmatch(path.name) or path.name == active_id or path.name in activated:
                    continue
                _config.ordinary(path)
                if not (path / 'generation.json').exists():
                    continue
                rows.append(self._safe_info(self._generation(path.name)[1]))
            return sorted(rows, key=lambda row: (row['createdAt'], row['generationId']), reverse=True)

    def status(self):
        with self.lock:
            generation = self._generation()
            with self._controls() as db:
                excluded = db.execute('SELECT count(*) FROM exclusions WHERE enabled=1').fetchone()[0]
            memory_counts = {}
            for memory in self.memories():
                effective = memory['effectiveStatus']
                memory_counts[effective] = memory_counts.get(effective, 0) + 1
            return dict(available=generation is not None, role=self.role,
                        generation=self._safe_info(generation[1]) if generation else None,
                        prepared=self.prepared_generations(), exclusions=excluded, memories=memory_counts,
                        projects=self.projects(), policyRevision=self.policy_revision(),
                        memorySelectionRevision=self.memory_selection_revision(),cleanupPending=getattr(self,'cleanup_pending',False),
                        syncRecoveryRequired=getattr(self,'recovery_required',False))

    def _control_snapshot(self):
        with self._controls() as db:
            return dict(exclusions=[dict(row) for row in db.execute('SELECT * FROM exclusions ORDER BY scope,value,origin')],
                        memories=[dict(row) for row in db.execute('SELECT * FROM memories ORDER BY id')],
                        projects=[dict(row) for row in db.execute('SELECT * FROM project_assignments ORDER BY scope,value')])

    def policy_revision(self):
        """Opaque change fence; callers need no memory text to reset prior context."""
        with self.lock:
            return hashlib.sha256(canonical(self._control_snapshot())).hexdigest()

    def _memory_selection(self):
        # Keep this snapshot aligned with the page's actual filters. Approval
        # decisions and bookkeeping timestamps cannot restart a source queue.
        with self._controls() as db:
            db.execute('BEGIN')
            rules = {(row['scope'], row['value']) for row in db.execute('SELECT scope,value FROM exclusions WHERE enabled=1')}
            assignments = {(row['scope'], row['value']): row['project']
                           for row in db.execute('SELECT scope,value,project FROM project_assignments')}
        revision = hashlib.sha256(canonical(dict(exclusions=sorted(rules),
            projects=sorted((scope, value, project) for (scope, value), project in assignments.items())))).hexdigest()
        return rules, assignments, revision

    def memory_selection_revision(self):
        """Opaque fence for exclusions/project changes, independent of reviews."""
        with self.lock:
            return self._memory_selection()[2]

    @staticmethod
    def _row(row):
        value = dict(row)
        value['metadata'] = json.loads(value['metadata'])
        value['memory_allowed'] = bool(value['memory_allowed'])
        value['retainedAbsent'] = not bool(value.pop('seen', 1))
        return value

    @staticmethod
    def _lineage(record):
        out = {('record', record['id'])}
        metadata = record.get('metadata', {})
        for scope, names in (('conversation', ('conversationId', 'conversationIds')),
                             ('asset', ('assetId', 'assetIds', 'attachmentId', 'attachmentIds'))):
            for name in names:
                values = metadata.get(name, [])
                if isinstance(values, str):
                    values = [values]
                if isinstance(values, list):
                    out.update((scope, value) for value in values if isinstance(value, str) and 0 < len(value) <= 500)
        return out

    @staticmethod
    def _restriction_lineage(record):
        metadata = record.get('metadata', {})
        out = set(metadata.get('restricted_source_ids', []))
        direct = metadata.get('conversationSourceId')
        if isinstance(direct, str) and HEX.fullmatch(direct):
            out.add(direct)
        for scope, value in HistoryStore._lineage(record):
            if scope == 'conversation':
                logical = value if value.startswith('group:') else ('group:' if record['kind'].lower().startswith('group') else 'conversation:') + value
                out.add(hashlib.sha256(logical.encode('utf-8')).hexdigest())
            elif scope == 'asset':
                out.add(hashlib.sha256(('asset:' + value).encode('utf-8')).hexdigest())
        return out

    @staticmethod
    def _scope_rules(record):
        lineage = HistoryStore._lineage(record)
        conversations = {(scope, value) for scope, value in lineage if scope == 'conversation'}
        assets = {(scope, value) for scope, value in lineage if scope == 'asset'}
        rules = (assets or conversations) if record['kind'] in ('document', 'attachment') else (conversations or assets)
        return rules or {('record', record['id'])}

    def _project_rules(self):
        with self._controls() as db:
            return {(row['scope'], row['value']): row['project'] for row in db.execute('SELECT * FROM project_assignments')}

    @staticmethod
    def _projected(record, assignments):
        lineage = HistoryStore._lineage(record)
        for scope in ('record', 'asset', 'conversation'):
            values = {assignments[(kind, value)] for kind, value in lineage if kind == scope and (kind, value) in assignments}
            if values:
                record['project'] = next(iter(values)) if len(values) == 1 else ''
                break
        return record

    def assign_project(self, source_id, project, generation_id=None):
        self._primary()
        need(isinstance(source_id, str) and HEX.fullmatch(source_id), 'Invalid history source ID.')
        need(isinstance(project, str) and len(project.encode('utf-8')) <= 200 and not any(ord(c) < 32 for c in project), 'Invalid project name.')
        project = project.strip()
        with self._writer():
            with self._index(self._view_generation(generation_id)) as db:
                row = db.execute('SELECT r.* FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE r.id=?', (source_id,)).fetchone()
                need(row is not None, 'History source is unavailable.')
                rules = self._scope_rules(self._row(row))
            with self._controls() as db:
                db.executemany('INSERT OR REPLACE INTO project_assignments VALUES(?,?,?,?)',
                               [(scope, value, project, utc()) for scope, value in rules])
            return dict(sourceId=source_id, project=project, rules=len(rules))

    def projects(self):
        with self.lock:
            assignments = self._project_rules()
            names = {value for value in assignments.values() if value}
            if self._generation() is not None:
                with self._index() as db:
                    names.update(row[0] for row in db.execute('SELECT DISTINCT r.project FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE r.project!=\'\''))
            return sorted(names, key=lambda value: (value.casefold(), value))

    def exclusion_binding(self, source_id, generation_id=None):
        """Resolve immutable lineage without bypassing source text permissions."""
        need(isinstance(source_id, str) and HEX.fullmatch(source_id), 'Invalid exclusion source.')
        generation = self._view_generation(generation_id)
        need(generation is not None, 'History source generation is unavailable.')
        with self._index(generation) as db:
            row = db.execute('SELECT r.* FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE r.id=?', (source_id,)).fetchone()
            need(row is not None, 'History exclusion source is unavailable.')
            source = self._row(row)
        rules = self._validated_rule_entries([dict(scope=s, value=v) for s, v in self._scope_rules(source)])
        return dict(sourceId=source_id, generationId=generation[1]['generationId'], revision=source['revision'],
                    rules=rules, lineageSha256=hashlib.sha256(canonical(rules)).hexdigest())

    def reserve_exclusion(self):
        need(self.role == 'replica', 'Only a replica reserves exclusion intents.')
        with self._writer(), self._controls() as db:
            db.execute('UPDATE exclusion_meta SET ordinal=ordinal+1 WHERE id=1')
            return db.execute('SELECT ordinal FROM exclusion_meta WHERE id=1').fetchone()[0]

    def queue_exclusion(self, binding, enabled, ordinal):
        need(self.role == 'replica' and type(enabled) is bool and type(ordinal) is int and ordinal > 0, 'Invalid exclusion intent.')
        rules = self._validated_rule_entries(binding['rules'])
        need(binding.get('lineageSha256') == hashlib.sha256(canonical(rules)).hexdigest(), 'Exclusion lineage differs.')
        need(GEN.fullmatch(binding.get('generationId', '')) and HEX.fullmatch(binding.get('revision', '')) and
             HEX.fullmatch(binding.get('sourceId', '')), 'Invalid frozen exclusion source.')
        with self._writer(), self._controls() as db:
            meta = db.execute('SELECT * FROM exclusion_meta WHERE id=1').fetchone()
            need(ordinal <= meta['ordinal'], 'Exclusion ordinal was not reserved.')
            applied = False
            for rule in rules:
                scope, value = rule['scope'], rule['value']
                previous = db.execute('SELECT ordinal FROM exclusion_versions WHERE scope=? AND value=?', (scope, value)).fetchone()
                if previous and previous[0] > ordinal:
                    continue
                payload = dict(replicaId=meta['replica_id'], ordinal=ordinal, enabled=enabled,
                               binding=binding, rule=rule)
                fingerprint = hashlib.sha256(canonical(payload)).hexdigest()
                payload['fingerprint'] = fingerprint
                db.execute('INSERT OR REPLACE INTO exclusions VALUES(?,?,\'local\',?,?)', (scope, value, int(enabled), utc()))
                db.execute('INSERT OR REPLACE INTO exclusion_sources VALUES(?,?,?)', (scope, value, binding['sourceId']))
                db.execute('INSERT OR REPLACE INTO exclusion_versions VALUES(?,?,?)', (scope, value, ordinal))
                db.execute('INSERT OR REPLACE INTO exclusion_outbox VALUES(?,?,?,?,?)',
                           (scope, value, ordinal, fingerprint, canonical(payload).decode()))
                applied = True
            if applied: db.execute('UPDATE exclusion_meta SET policy_revision=policy_revision+1 WHERE id=1')
            # A newer explicit, resolved choice supersedes its unresolved legacy hint.
            db.execute('DELETE FROM exclusion_unresolved WHERE id=?', (binding['sourceId'],))
        return dict(sourceId=binding['sourceId'], enabled=enabled, ordinal=ordinal, status='pending', ruleEntries=rules)

    def exclusion_intents(self):
        with self._controls() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM exclusion_outbox ORDER BY ordinal,scope,value')]

    def acknowledge_exclusion(self, payload, result):
        need(result.get('fingerprint') == payload['fingerprint'] and result.get('ordinal') == payload['ordinal'] and
             result.get('status') in ('applied', 'acknowledged', 'stale'), 'Exclusion acknowledgment differs.')
        with self._writer(), self._controls() as db:
            db.execute('DELETE FROM exclusion_outbox WHERE scope=? AND value=? AND ordinal=? AND fingerprint=?',
                       (payload['rule']['scope'], payload['rule']['value'], payload['ordinal'], payload['fingerprint']))

    def exclusion_sync_state(self):
        with self._controls() as db:
            return dict(pendingCount=db.execute('SELECT count(*) FROM exclusion_outbox').fetchone()[0],
                        unresolvedCount=db.execute('SELECT count(*) FROM exclusion_unresolved').fetchone()[0],
                        localPolicyRevision=db.execute('SELECT policy_revision FROM exclusion_meta WHERE id=1').fetchone()[0])

    def local_exclusion_policy(self):
        with self._controls() as db:
            db.execute('BEGIN')
            rules = {(row[0], row[1]) for row in db.execute("SELECT scope,value FROM exclusions WHERE enabled=1 AND origin='local'")}
            revision = db.execute('SELECT policy_revision FROM exclusion_meta WHERE id=1').fetchone()[0]
        return rules, revision

    def migrate_exclusions(self, legacy):
        """Keep ambiguous legacy intentions as evidence; never invent their order."""
        need(self.role == 'replica' and isinstance(legacy, dict), 'Invalid legacy exclusion queue.')
        with self._controls() as db:
            if db.execute('SELECT migrated FROM exclusion_meta WHERE id=1').fetchone()[0]:
                return
        resolved, unresolved = [], []
        for source_id, value in legacy.items():
            try:
                body = value if isinstance(value, dict) else dict(id=source_id, enabled=value)
                need(body.get('id') == source_id and type(body.get('enabled', True)) is bool, 'Malformed legacy intent.')
                binding = self.exclusion_binding(source_id, body.get('generation'))
                resolved.append((binding, body.get('enabled', True), value))
            except (ValueError, KeyError, TypeError, OSError):
                unresolved.append((source_id, value, 'source_binding_unavailable'))
        conflicts = set()
        choices = {}
        for binding, enabled, _ in resolved:
            for rule in binding['rules']:
                key = (rule['scope'], rule['value'])
                if key in choices and choices[key] != enabled:
                    conflicts.add(key)
                choices[key] = enabled
        # Queue each valid operation through the same atomic rule/outbox path.
        # Completion is last: a crash only repeats idempotent desired choices.
        for binding, enabled, raw in resolved:
            with self._controls() as db:
                rows = {(r[0], r[1]): bool(r[2]) for r in db.execute("SELECT scope,value,enabled FROM exclusions WHERE origin='local'")}
            keys = {(r['scope'], r['value']) for r in binding['rules']}
            if keys.intersection(conflicts) or any(key in rows and rows[key] != enabled for key in keys):
                unresolved.append((binding['sourceId'], raw, 'conflicting_legacy_intent'))
            else:
                self.queue_exclusion(binding, enabled, self.reserve_exclusion())
        with self._writer(), self._controls() as db:
            for source_id, payload, reason in unresolved:
                db.execute('INSERT OR REPLACE INTO exclusion_unresolved VALUES(?,?,?)',
                           (str(source_id), canonical(payload).decode(), reason))
            db.execute('UPDATE exclusion_meta SET migrated=1 WHERE id=1')

    def apply_exclusion_intent(self, payload, paired_replica):
        self._primary()
        need(isinstance(payload, dict) and set(payload) == {'replicaId','ordinal','enabled','binding','rule','fingerprint'}, 'Invalid fenced exclusion.')
        need(isinstance(paired_replica, str) and paired_replica and GEN.fullmatch(payload.get('replicaId', '')), 'Authenticated exclusion pair required.')
        need(type(payload['ordinal']) is int and payload['ordinal'] > 0 and type(payload['enabled']) is bool, 'Invalid exclusion version.')
        unsigned = dict(payload); fingerprint = unsigned.pop('fingerprint')
        need(fingerprint == hashlib.sha256(canonical(unsigned)).hexdigest(), 'Exclusion payload fingerprint differs.')
        rule = self._validated_rule_entries([payload['rule']])[0]
        scope, value = rule['scope'], rule['value']
        receipt_identity=payload['replicaId']
        with self._writer(), self._controls() as db:
            enrolled = db.execute('SELECT replica_id FROM exclusion_pairings WHERE caller=?', (paired_replica,)).fetchone()
            need(enrolled is None or enrolled[0] == payload['replicaId'], 'Replica ownership changed; explicitly pair the replacement before syncing.')
            db.execute('INSERT OR IGNORE INTO exclusion_pairings VALUES(?,?)', (paired_replica,payload['replicaId']))
            prior = db.execute('SELECT * FROM exclusion_receipts WHERE replica_id=? AND scope=? AND value=?', (receipt_identity,scope,value)).fetchone()
            if prior and prior['ordinal'] >= payload['ordinal']:
                if prior['ordinal'] == payload['ordinal']:
                    need(prior['fingerprint'] == fingerprint, 'Exclusion version was reused with different content.')
                    return json.loads(prior['response'])
                return dict(status='stale', ordinal=payload['ordinal'], fingerprint=fingerprint)
            frozen = payload['binding']
            binding = self.exclusion_binding(frozen['sourceId'], frozen['generationId'])
            need(binding == frozen and rule in binding['rules'], 'Frozen exclusion lineage is unavailable or differs.')
            db.execute('INSERT OR REPLACE INTO exclusions VALUES(?,?,\'local\',?,?)', (scope,value,int(payload['enabled']),utc()))
            db.execute('INSERT OR REPLACE INTO exclusion_sources VALUES(?,?,?)', (scope,value,binding['sourceId']))
            response = dict(status='applied', ordinal=payload['ordinal'], fingerprint=fingerprint)
            db.execute('INSERT OR REPLACE INTO exclusion_receipts VALUES(?,?,?,?,?,?)',
                       (receipt_identity,scope,value,payload['ordinal'],fingerprint,canonical(response).decode()))
            return response

    def _rules(self):
        with self._controls() as db:
            return {(row['scope'], row['value']) for row in db.execute('SELECT scope,value FROM exclusions WHERE enabled=1')}

    def _allowed(self, record, rules):
        return not self._lineage(record).intersection(rules)

    def source(self, source_id, revision=None, generation_id=None):
        need(isinstance(source_id, str) and HEX.fullmatch(source_id), 'Invalid history source ID.')
        need(revision is None or isinstance(revision, str) and HEX.fullmatch(revision), 'Invalid history revision.')
        with self.lock:
            generation = self._view_generation(generation_id)
            if generation is None:
                return None
            with self._index(generation) as db:
                if revision:
                    row = db.execute('SELECT r.*,1 AS seen FROM records r WHERE id=? AND revision=?', (source_id, revision)).fetchone()
                else:
                    row = db.execute('SELECT r.*,h.seen FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE r.id=?', (source_id,)).fetchone()
                if row is None:
                    return None
                record = self._projected(self._row(row), self._project_rules())
                return record if self._allowed(record, self._rules()) else None

    def exclude(self, source_id, enabled=True, generation_id=None, caller_identity=None):
        need(type(enabled) is bool, 'Exclusion state must be boolean.')
        need(isinstance(source_id, str) and HEX.fullmatch(source_id), 'Invalid history source ID.')
        with self._writer():
            if caller_identity:
                with self._controls() as controls:
                    need(controls.execute('SELECT 1 FROM exclusion_pairings WHERE caller=?',(caller_identity,)).fetchone() is None,
                         'This pair requires fenced exclusion intents; legacy mutations are refused.')
            with self._index(self._view_generation(generation_id)) as db:
                row = db.execute('SELECT r.* FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE r.id=?', (source_id,)).fetchone()
                need(row is not None, 'History source is unavailable.')
                record = self._row(row)
            rules = self._scope_rules(record)
            entries = self._validated_rule_entries([dict(scope=scope, value=value) for scope, value in sorted(rules)])
            with self._controls() as db:
                db.executemany('INSERT OR REPLACE INTO exclusions VALUES(?,?,\'local\',?,?)',
                               [(scope, value, int(enabled), utc()) for scope, value in rules])
                if self.role == 'replica': db.execute('UPDATE exclusion_meta SET policy_revision=policy_revision+1 WHERE id=1')
                db.executemany('INSERT OR REPLACE INTO exclusion_sources VALUES(?,?,?)',
                               [(scope, value, source_id) for scope, value in rules])
            return dict(sourceId=source_id, enabled=enabled, rules=len(rules), ruleEntries=entries)

    @staticmethod
    def _validated_rule_entries(rules):
        need(isinstance(rules, list) and 1 <= len(rules) <= 100, 'Choose one to one hundred exclusion rules.')
        result, seen = [], set()
        for rule in rules:
            need(isinstance(rule, dict) and set(rule) == {'scope', 'value'}, 'Exclusion rules accept only scope and value.')
            scope, value = rule['scope'], rule['value']
            need(scope in ('record', 'conversation', 'asset') and isinstance(value, str) and 0 < len(value) <= 500 and
                 not any(ord(c) < 32 for c in value) and (scope != 'record' or HEX.fullmatch(value)), 'Invalid exclusion rule identity.')
            need((scope, value) not in seen, 'Duplicate exclusion rule.')
            seen.add((scope, value)); result.append(dict(scope=scope, value=value))
        return sorted(result, key=lambda rule: (rule['scope'], rule['value']))

    def apply_exclusion_rules(self, rules, enabled=True, source_id=None):
        """Apply paired-primary metadata locally without importing prepared text."""
        need(self.role == 'replica', 'Only a replica may receive exclusion rules.')
        need(type(enabled) is bool, 'Exclusion state must be boolean.')
        need(source_id is None or isinstance(source_id, str) and HEX.fullmatch(source_id), 'Invalid exclusion source hint.')
        entries = self._validated_rule_entries(rules)
        with self._writer():
            with self._controls() as db:
                db.executemany('INSERT OR REPLACE INTO exclusions VALUES(?,?,\'local\',?,?)',
                               [(entry['scope'], entry['value'], int(enabled), utc()) for entry in entries])
                db.execute('UPDATE exclusion_meta SET policy_revision=policy_revision+1 WHERE id=1')
                if source_id is not None:
                    db.executemany('INSERT OR REPLACE INTO exclusion_sources VALUES(?,?,?)',
                                   [(entry['scope'], entry['value'], source_id) for entry in entries])
            return dict(sourceId=source_id, enabled=enabled, rules=len(entries), ruleEntries=entries)

    def exclude_record(self, record, enabled=True):
        """Local-first prepared exclusion; the authenticated caller supplies source data."""
        need(self.role == 'replica', 'Only a replica may apply a prepared source exclusion.')
        record = normalized_record(record)
        entries = [dict(scope=scope, value=value) for scope, value in self._scope_rules(record)]
        return self.apply_exclusion_rules(entries, enabled, source_id=record['id'])

    def exclusions(self, generation_id=None):
        with self.lock, self._controls() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM exclusions ORDER BY scope,value,origin')]
            representatives = {(row['scope'], row['value']): row['source_id'] for row in db.execute('SELECT * FROM exclusion_sources')}
            if generation_id is None and all((row['scope'], row['value']) in representatives for row in rows):
                # Persisted hints win over archive-derived representatives below.
                # Once every rule is resolved, scanning generations cannot alter
                # the result. Explicit generation requests retain verification.
                for row in rows:
                    row['sourceId'] = representatives[(row['scope'], row['value'])]
                return rows
            generation = self._view_generation(generation_id)
            generations = [generation] if generation else []
            if generation_id is None:
                generations += [self._view_generation(row['generationId']) for row in self.prepared_generations()]
            for generation in generations:
                with self._index(generation) as index:
                    for row in index.execute('SELECT r.id,r.kind,r.metadata FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision'):
                        record = dict(row); record['metadata'] = json.loads(record['metadata'])
                        for rule in self._scope_rules(record):
                            representatives.setdefault(rule, record['id'])
            for row in rows:
                row['sourceId'] = representatives.get((row['scope'], row['value']))
            return rows

    @staticmethod
    def _date_filter(date_from, date_to):
        out = []
        for index, value in enumerate((date_from, date_to)):
            if value is None:
                out.append(None); continue
            need(isinstance(value, str), 'Dates must be ISO text.')
            try:
                parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
                if index == 1 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                out.append(parsed.astimezone(dt.timezone.utc).isoformat())
            except ValueError as exc:
                raise ValueError('Invalid history date filter.') from exc
        need(not all(out) or out[0] <= out[1], 'History date range is reversed.')
        return out

    def search(self, query, project='', limit=8, date_from=None, date_to=None, generation_id=None):
        need(isinstance(query, str) and 0 < len(query.encode('utf-8')) <= 8000, 'Enter a bounded history query.')
        need(isinstance(project, str) and len(project) <= 200 and type(limit) is int and 1 <= limit <= 30, 'Invalid history search options.')
        dates = self._date_filter(date_from, date_to)
        with self.lock:
            generation = self._view_generation(generation_id)
            if generation is None:
                return []
            rules, assignments, scores, rows = self._rules(), self._project_rules(), {}, {}
            with self._index(generation) as db:
                def eligible(row):
                    record = self._projected(self._row(row), assignments)
                    return (not project or record['project'] == project) and \
                        (not dates[0] or record['timestamp'] and record['timestamp'] >= dates[0]) and \
                        (not dates[1] or record['timestamp'] and record['timestamp'] <= dates[1]) and self._allowed(record, rules)
                terms = re.findall(r'\w+', query)[:32]
                if terms:
                    match = ' OR '.join('"' + word.replace('"', '""') + '"' for word in terms)
                    result = db.execute('''SELECT r.id,r.revision,r.kind,r.title,c.text,r.timestamp,r.project,r.memory_allowed,r.metadata,h.seen,c.id AS chunkId,c.start,c.end
                        FROM fts f JOIN chunks c ON f.id=c.id JOIN records r ON c.source_id=r.id AND c.revision=r.revision
                        JOIN heads h ON h.id=r.id AND h.revision=r.revision WHERE fts MATCH ? ORDER BY bm25(fts)''', (match,))
                    rank = 0
                    seen = set()
                    for row in result:
                        if not eligible(row) or row['id'] in seen:
                            continue
                        seen.add(row['id'])
                        rank += 1
                        key = row['id']; rows[key] = self._row(row)
                        scores[key] = scores.get(key, 0) + 1 / (60 + rank)
                        if rank >= max(40, limit * 4):
                            break
                query_embedder = self.embedder
                if generation[1].get('semantic') and query_embedder is not None:
                    need(generation[1].get('embeddingIdentity') == self._embedding_identity(query_embedder), 'Embedding identity differs from the active history index.')
                    self._semantic(db, generation, query, eligible, scores, rows, limit, query_embedder)
                ordered = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
                out = []
                for key in ordered:
                    row = self._projected(rows[key], assignments)
                    out.append(dict(id=key, sourceId=key, revision=row['revision'], kind=row['kind'], title=row['title'],
                                    timestamp=row['timestamp'], project=row['project'], excerpt=row['text'][:1600],
                                    chunkId=row.get('chunkId'), start=row.get('start', 0), end=row.get('end', len(row['text'])),
                                    score=scores[key], retainedAbsent=row['retainedAbsent'], memory_allowed=row['memory_allowed']))
                return out

    def _semantic(self, db, generation, query, eligible, scores, rows, limit, embedder):
        import numpy as np
        query_fn = getattr(embedder, 'query', None)
        values = np.asarray(query_fn(query) if query_fn else embedder([query]), dtype=np.float32)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        need(values.shape == (1, generation[1]['dimensions']) and np.isfinite(values).all(), 'Invalid query embedding.')
        length = np.linalg.norm(values[0]); need(length > 0, 'Zero-length query embedding.')
        vector = values[0] / length
        matrix = np.load(_config.ordinary(generation[0] / 'vectors.npy'), mmap_mode='r', allow_pickle=False)
        need(matrix.ndim == 2 and matrix.shape[1] == len(vector) and str(matrix.dtype) == 'float32', 'History vector shape differs.')
        candidates = {}
        try:
            # Apply permission/date/project filters before top-k, including every block.
            for start in range(0, len(matrix), 2048):
                stop = min(len(matrix), start + 2048)
                block = matrix[start:stop] @ vector
                block_rows = db.execute('''SELECT r.id,r.revision,r.kind,r.title,c.text,r.timestamp,r.project,r.memory_allowed,r.metadata,h.seen,c.id AS chunkId,c.start,c.end,v.position
                    FROM vector_ids v JOIN chunks c ON v.id=c.id JOIN records r ON c.source_id=r.id AND c.revision=r.revision
                    JOIN heads h ON h.id=r.id AND h.revision=r.revision WHERE v.position>=? AND v.position<?''', (start, stop))
                for row in block_rows:
                    if eligible(row):
                        score = float(block[row['position'] - start])
                        if row['id'] not in candidates or score > candidates[row['id']][0]:
                            candidates[row['id']] = (score, self._row(row))
                candidates = dict(sorted(candidates.items(), key=lambda item: (-item[1][0], item[0]))[:max(40, limit * 4)])
            for rank, (key, (_, row)) in enumerate(sorted(candidates.items(), key=lambda item: (-item[1][0], item[0])), 1):
                rows.setdefault(key, row)
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
        finally:
            del matrix

    def _memory_eligible(self, source):
        meta = source.get('metadata', {})
        if source['kind'].lower() in ('conversation', 'message', 'conversation_message') and meta.get('role') != 'user':
            return False
        return source['memory_allowed'] and not source['kind'].lower().startswith('group') and \
            not any(meta.get(key) for key in ('restricted_source_ids', 'do_not_remember', 'doNotRemember', 'groupChatId', 'groupId', 'speakerUnknown'))

    @staticmethod
    def _memory_priority(source):
        if source['kind'].lower() in ('conversation', 'message', 'conversation_message') and source['metadata'].get('role') == 'user':
            return 0
        return 1 if source['kind'].lower() == 'page' else 2

    def memory_input_page(self, project='', limit=20, after=None):
        """Bound scanned candidates and preserve a deterministic generation cursor.

        User conversations precede Pages and other eligible source categories.
        Filtered candidates also advance the cursor: an empty non-final page
        means the caller must retain nextCursor, not restart at the beginning.
        Text is fetched only for selected sources, never for the entire archive.
        """
        self._primary()
        need(type(limit) is int and 1 <= limit <= 200 and isinstance(project, str) and len(project) <= 200 and '\0' not in project,
             'Invalid memory input options.')
        need(after is None or isinstance(after, dict) and set(after) == {'generationId', 'afterId'} and
             isinstance(after['generationId'], str) and GEN.fullmatch(after['generationId']) and
             isinstance(after['afterId'], str) and HEX.fullmatch(after['afterId']), 'Invalid memory input cursor.')
        with self.lock:
            generation = self._generation()
            generation_id = generation[1]['generationId'] if generation else None
            need(after is None or after['generationId'] == generation_id, 'Memory input cursor belongs to a different generation.')
            rules, assignments, selection_revision = self._memory_selection()
            if generation is None:
                return dict(sources=[], nextCursor=None, generationId=None, exhausted=True, scanned=0,
                            selectionRevision=selection_revision)
            output, scanned, consumed = [], 0, after['afterId'] if after else None
            # These expressions match _memory_priority. SQL LIMIT bounds rows
            # crossing into Python; only small metadata fields are inspected.
            priority_sql = "CASE WHEN lower(r.kind) IN ('conversation','message','conversation_message') AND json_extract(r.metadata,'$.role')='user' THEN 0 WHEN lower(r.kind)='page' THEN 1 ELSE 2 END"
            with self._index(generation) as db:
                priority = 0
                if after:
                    row = db.execute('SELECT r.kind,r.metadata FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE r.id=?', (after['afterId'],)).fetchone()
                    need(row is not None, 'Memory input cursor source is unavailable.')
                    priority = self._memory_priority(dict(kind=row['kind'], metadata=json.loads(row['metadata'])))
                for bucket in range(priority, 3):
                    start = after['afterId'] if after and bucket == priority else ''
                    rows = db.execute('''SELECT r.id,r.revision,r.kind,r.title,r.timestamp,r.project,r.memory_allowed,r.metadata,h.seen
                        FROM heads h JOIN records r ON r.id=h.id AND r.revision=h.revision
                        WHERE r.memory_allowed=1 AND r.id>? AND (''' + priority_sql + ''')=? ORDER BY r.id LIMIT ?''',
                                      (start, bucket, MEMORY_SCAN_LIMIT - scanned + 1))
                    for row in rows:
                        if scanned == MEMORY_SCAN_LIMIT:
                            return dict(sources=output, nextCursor=dict(generationId=generation_id, afterId=consumed),
                                        generationId=generation_id, exhausted=False, scanned=scanned, selectionRevision=selection_revision)
                        source = self._projected(self._row(row), assignments)
                        eligible = self._memory_eligible(source) and self._allowed(source, rules) and (not project or source['project'] == project)
                        if eligible and len(output) == limit:
                            # Lookahead proves another eligible candidate exists,
                            # but does not consume it or skip it on the next page.
                            return dict(sources=output, nextCursor=dict(generationId=generation_id, afterId=consumed),
                                        generationId=generation_id, exhausted=False, scanned=scanned + 1, selectionRevision=selection_revision)
                        scanned += 1
                        consumed = source['id']
                        if eligible:
                            source['text'] = db.execute('SELECT text FROM records WHERE id=? AND revision=?',
                                                        (source['id'], source['revision'])).fetchone()[0]
                            output.append(source)
            return dict(sources=output, nextCursor=None, generationId=generation_id, exhausted=True, scanned=scanned,
                        selectionRevision=selection_revision)

    def memory_inputs(self, source_ids=None, project='', limit=20):
        self._primary()
        need(type(limit) is int and 1 <= limit <= 200, 'Invalid memory input limit.')
        if source_ids is None:
            output, after = [], None
            while len(output) < limit:
                page = self.memory_input_page(project=project, limit=limit - len(output), after=after)
                output.extend(page['sources'])
                if page['exhausted']:
                    break
                after = page['nextCursor']
            return output
        with self.lock:
            generation = self._generation()
            if generation is None:
                return []
            need(isinstance(source_ids,list) and len(source_ids) <= 200 and all(isinstance(x,str) and HEX.fullmatch(x) for x in source_ids),
                 'Invalid memory source identities.')
            with self._controls() as controls:
                controls.execute('BEGIN')
                rules = {(r[0],r[1]) for r in controls.execute('SELECT scope,value FROM exclusions WHERE enabled=1')}
                assignments = {(r[0],r[1]):r[2] for r in controls.execute('SELECT scope,value,project FROM project_assignments')}
            sources = {}
            if source_ids:
                with self._index(generation) as index:
                    query = 'SELECT r.*,h.seen FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE r.id IN (' + ','.join('?' for _ in source_ids) + ')'
                    for row in index.execute(query,source_ids):
                        source = self._projected(self._row(row),assignments)
                        if self._allowed(source,rules): sources[source['id']] = source
            out = []
            for source_id in source_ids:
                source = sources.get(source_id)
                if source and self._memory_eligible(source) and (not project or source['project'] == project):
                    out.append(source)
                if len(out) == limit:
                    break
            return out

    def propose_memory(self, text, source_ids, category='fact', project='', request_id=None, expected_revisions=None):
        self._primary()
        need(isinstance(text, str) and 0 < len(text.encode('utf-8')) <= 16000 and '\0' not in text, 'Enter bounded memory text.')
        need(isinstance(category, str) and 0 < len(category) <= 80 and isinstance(project, str) and len(project) <= 200, 'Invalid memory category or project.')
        need(isinstance(source_ids, list) and 1 <= len(source_ids) <= 30 and all(isinstance(key, str) and HEX.fullmatch(key) for key in source_ids) and len(set(source_ids)) == len(source_ids), 'Memory requires unique source references.')
        need(request_id is None or isinstance(request_id, str) and GEN.fullmatch(request_id), 'Invalid memory proposal request ID.')
        need(expected_revisions is None or isinstance(expected_revisions, dict) and set(expected_revisions) == set(source_ids) and
             all(isinstance(value, str) and HEX.fullmatch(value) for value in expected_revisions.values()), 'Expected memory source revisions must cover every source.')
        fingerprint = hashlib.sha256(canonical(dict(text=text, sourceIds=source_ids, category=category, project=project,
                                                    expectedRevisions=expected_revisions))).hexdigest()
        with self._writer():
            if request_id is not None:
                with self._controls() as db:
                    prior = db.execute('SELECT fingerprint,memory_id FROM memory_requests WHERE request_id=?', (request_id,)).fetchone()
                if prior:
                    need(prior['fingerprint'] == fingerprint, 'Memory proposal request payload differs from its original request.')
                    memory = self._memory_by_id(prior['memory_id'])
                    need(memory is not None, 'Retained memory request has no proposal; review is required.')
                    return memory
            sources = [self.source(key) for key in source_ids]
            need(all(source and self._memory_eligible(source) for source in sources), 'A source is excluded or unavailable for memory.')
            need(not project or all(source['project'] == project for source in sources), 'Memory source project differs.')
            need(expected_revisions is None or all(source['revision'] == expected_revisions[source['id']] for source in sources),
                 'Memory source revision changed; generate a new proposal.')
            key, now = uuid.uuid4().hex, utc()
            refs = [dict(id=source['id'], revision=source['revision']) for source in sources]
            with self._controls() as db:
                db.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?,?)', (key, text, category, project, 'proposed', canonical(refs).decode(), now, now))
                if request_id is not None:
                    db.execute('INSERT INTO memory_requests VALUES(?,?,?)', (request_id, fingerprint, key))
            return self._memory_by_id(key)

    def _memory_values(self, rows, controls=None):
        """One consistent control snapshot and one immutable index for a listing."""
        rows = list(rows)
        if controls is None:
            with self._controls() as snapshot:
                snapshot.execute('BEGIN')
                return self._memory_values(rows,snapshot)
        ids = sorted({ref['id'] for row in rows for ref in json.loads(row['refs'])})
        generation = self._generation()
        rules = {(r[0],r[1]) for r in controls.execute('SELECT scope,value FROM exclusions WHERE enabled=1')}
        assignments = {(r[0],r[1]):r[2] for r in controls.execute('SELECT scope,value,project FROM project_assignments')}
        sources = {}
        if generation is not None and ids:
            with self._index(generation) as index:
                for start in range(0,len(ids),800):
                    batch = ids[start:start+800]
                    query = 'SELECT r.id,r.revision,r.kind,r.project,r.memory_allowed,r.metadata,h.seen FROM records r JOIN heads h ON r.id=h.id AND r.revision=h.revision WHERE r.id IN (' + ','.join('?' for _ in batch) + ')'
                    for row in index.execute(query,batch):
                        source = self._projected(self._row(row),assignments)
                        if self._allowed(source,rules): sources[source['id']] = source
        return [self._memory_value(row,sources) for row in rows]

    def _memory_value(self, row, sources=None):
        value = dict(row); value['sources'] = json.loads(value.pop('refs'))
        effective = value['status']
        if effective != 'rejected':
            for ref in value['sources']:
                source = sources.get(ref['id']) if sources is not None else self.source(ref['id'])
                if not source or not self._memory_eligible(source):
                    effective = 'blocked'; break
                if source['revision'] != ref['revision'] or value['project'] and source['project'] != value['project']:
                    effective = 'stale'
        value['effectiveStatus'] = effective
        return value

    def _memory_by_id(self, memory_id):
        # A retry reads only its own proposal, even after many background batches.
        with self.lock:
            with self._controls() as db:
                db.execute('BEGIN')
                row = db.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
                return self._memory_values([row],db)[0] if row is not None else None

    def memory_requests(self, request_ids):
        self._primary()
        need(isinstance(request_ids,list) and len(request_ids) <= 20 and all(isinstance(x,str) and GEN.fullmatch(x) for x in request_ids),
             'Invalid memory request lookup.')
        with self._controls() as db:
            rows = db.execute('SELECT r.request_id,m.id,m.status FROM memory_requests r JOIN memories m ON r.memory_id=m.id WHERE r.request_id IN (' + ','.join('?' for _ in request_ids) + ')',request_ids).fetchall() if request_ids else []
        return {row['request_id']:dict(id=row['id'],status=row['status']) for row in rows}

    def memories(self, status=None):
        need(status is None or status in ('proposed', 'approved', 'rejected', 'blocked', 'stale'), 'Invalid memory status.')
        with self.lock, self._controls() as db:
            db.execute('BEGIN')
            out = []
            for value in self._memory_values(db.execute('SELECT * FROM memories ORDER BY created,id'),db):
                if status is None or value['effectiveStatus'] == status:
                    out.append(value)
            return out

    def memories_page(self, limit=100, after=None, status=None):
        need(type(limit) is int and 1 <= limit <= 100 and (after is None or isinstance(after,str) and GEN.fullmatch(after)),
             'Invalid memory page.')
        need(status is None or status in ('proposed','approved','rejected','blocked','stale'), 'Invalid memory status.')
        with self.lock, self._controls() as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT * FROM memories WHERE id>? ORDER BY id LIMIT ?', (after or '',limit+1)).fetchall()
            total = db.execute('SELECT count(*) FROM memories').fetchone()[0]
            values = self._memory_values(rows[:limit],db)
            if status is not None: values = [v for v in values if v['effectiveStatus'] == status]
            return dict(memories=values,total=total,nextCursor=rows[limit-1]['id'] if len(rows)>limit else None)

    def review_memory(self, memory_id, action, text=None):
        self._primary()
        need(isinstance(memory_id, str) and GEN.fullmatch(memory_id), 'Invalid memory ID.')
        need(action in ('approve', 'reject', 'edit'), 'Choose approve, reject or edit.')
        if text is not None:
            need(isinstance(text, str) and 0 < len(text.encode('utf-8')) <= 16000 and '\0' not in text, 'Enter bounded memory text.')
        with self._writer():
            memory = self._memory_by_id(memory_id)
            need(memory is not None, 'Memory proposal is unavailable.')
            if action == 'approve':
                sources = [self.source(ref['id']) for ref in memory['sources']]
                need(all(source and self._memory_eligible(source) and source['revision'] == ref['revision']
                         and (not memory['project'] or source['project'] == memory['project'])
                         for source, ref in zip(sources, memory['sources'])),
                     'Memory sources changed or are restricted; create a new proposal.')
            need(action != 'edit' or text is not None, 'Edited memory text is required.')
            status = {'approve': 'approved', 'reject': 'rejected', 'edit': 'proposed'}[action]
            with self._controls() as db:
                db.execute('UPDATE memories SET text=?,status=?,updated=? WHERE id=?', (text if text is not None else memory['text'], status, utc(), memory_id))
            return self._memory_by_id(memory_id)

    def snapshot_manifest(self):
        self._primary()
        with self._writer('export'):
            generation = self._generation(); need(generation is not None, 'No history generation is active.')
            path, info = generation
            controls = self._control_snapshot()
            policy_revision = hashlib.sha256(canonical(controls)).hexdigest()
            cache_key = hashlib.sha256(canonical([info['generationId'], policy_revision])).hexdigest()
            cache_path = self.root / 'SNAPSHOT-CACHE.json'
            if cache_path.exists():
                cache = json_read(cache_path)
                if cache.get('key') == cache_key:
                    snapshot_id = cache.get('snapshotId')
                    need(isinstance(snapshot_id, str) and HEX.fullmatch(snapshot_id), 'Retained snapshot cache identity differs.')
                    cached = json_read(self.root / 'exports' / snapshot_id / 'MANIFEST.json')
                    unsigned = dict(cached); unsigned.pop('snapshotId', None)
                    need(cached.get('snapshotId') == snapshot_id and hashlib.sha256(canonical(unsigned)).hexdigest() == snapshot_id,
                         'Retained snapshot manifest differs.')
                    return cached
            stage = self.root / 'exports' / ('.' + uuid.uuid4().hex)
            stage.mkdir()
            for name in ('index.sqlite3', 'generation.json', 'vectors.npy'):
                if (path / name).exists():
                    _config.ordinary(path / name)
                    shutil.copyfile(path / name, stage / name)
            json_write(stage / 'controls.json', controls)
            files = [dict(path=file.name, bytes=file.stat().st_size, sha256=file_hash(file)) for file in sorted(stage.iterdir())]
            manifest = dict(format=SNAPSHOT, generationId=info['generationId'], createdAt=info['createdAt'], files=files)
            manifest['snapshotId'] = hashlib.sha256(canonical(manifest)).hexdigest()
            json_write(stage / 'MANIFEST.json', manifest)
            target = self.root / 'exports' / manifest['snapshotId']
            if target.exists():
                need(json_read(target / 'MANIFEST.json') == manifest, 'Retained snapshot identity differs.')
                for file in stage.iterdir():
                    _config.ordinary(file).unlink()
                stage.rmdir()
            else:
                os.replace(stage, target)
            json_write(cache_path, dict(key=cache_key, snapshotId=manifest['snapshotId']))
            return manifest

    def snapshot_read_chunk(self, snapshot_id, path, offset, length=1024 * 1024):
        need(isinstance(snapshot_id, str) and HEX.fullmatch(snapshot_id), 'Invalid history snapshot ID.')
        need(path in FILES and type(offset) is int and offset >= 0 and type(length) is int and 1 <= length <= MAX_CHUNK,
             'Invalid history snapshot chunk.')
        root = _config.ordinary(self.root / 'exports' / snapshot_id)
        manifest = json_read(root / 'MANIFEST.json')
        row = next((row for row in manifest['files'] if row['path'] == path), None)
        need(row is not None and offset <= row['bytes'], 'Snapshot chunk is outside the manifest.')
        with open(_config.ordinary(root / path), 'rb') as stream:
            stream.seek(offset)
            return stream.read(min(length, row['bytes'] - offset))

    def _cleanup_sync_stage(self, stage, receipt):
        stage = Path(stage)
        need(stage.parent == self.root / 'generations' and stage.name.startswith('.') and GEN.fullmatch(stage.name[1:]),
             'Sync cleanup target differs from the owned stage.')
        if stage.exists():
            _config.ordinary(stage)
            entries = list(stage.iterdir())
            need(all(item.name in FILES and _config.ordinary(item).is_file() for item in entries), 'Unexpected sync staging contents; cleanup requires review.')
            for item in entries:
                _config.ordinary(item).unlink()
            stage.rmdir()
        if receipt.exists():
            _config.ordinary(receipt).unlink()

    def _recover_sync_stages(self):
        self.cleanup_pending = False
        self.recovery_required = False
        receipts = _config.ordinary(self.root / 'sync-stages', missing=True)
        receipts.mkdir(exist_ok=True)
        with self._writer('sync'):
            for receipt in receipts.glob('*.json'):
                try:
                    value = json_read(receipt)
                    need(value.get('format') == 'interlinked-history-sync-stage-v1' and value.get('root') == str(self.root) and
                         GEN.fullmatch(receipt.stem) and value.get('stage') == '.' + receipt.stem and
                         HEX.fullmatch(value.get('snapshotId', '')), 'Invalid sync stage ownership receipt.')
                    if value.get('phase') == 'recovery_required':
                        self.cleanup_pending = True
                        self.recovery_required = True
                        continue
                    self._cleanup_sync_stage(self.root / 'generations' / value['stage'], receipt)
                except (ValueError, OSError, KeyError, TypeError):
                    self.cleanup_pending = True

    def install_snapshot(self, manifest, reader, cancel=None, progress=None):
        """Replica receives bytes via reader(path, offset, length), never caller paths."""
        need(self.role == 'replica', 'Snapshot installation is only for a replica.')
        need(isinstance(manifest, dict) and manifest.get('format') == SNAPSHOT and callable(reader), 'Invalid history snapshot.')
        copied = dict(manifest); snapshot_id = copied.pop('snapshotId', None)
        need(isinstance(snapshot_id, str) and HEX.fullmatch(snapshot_id) and hashlib.sha256(canonical(copied)).hexdigest() == snapshot_id,
             'History snapshot manifest hash differs.')
        generation_id = manifest.get('generationId')
        need(isinstance(generation_id, str) and GEN.fullmatch(generation_id), 'Invalid snapshot generation ID.')
        files = manifest.get('files')
        need(isinstance(files, list) and 3 <= len(files) <= 4 and {row.get('path') for row in files} >= {'index.sqlite3', 'generation.json', 'controls.json'}, 'Incomplete history snapshot.')
        need(len({row.get('path') for row in files}) == len(files), 'Duplicate snapshot file.')
        total = 0
        for row in files:
            need(row.get('path') in FILES and type(row.get('bytes')) is int and 0 <= row['bytes'] <= 64 * 1024 ** 3 and isinstance(row.get('sha256'), str) and HEX.fullmatch(row['sha256']), 'Invalid snapshot file metadata.')
            total += row['bytes']
        need(total <= 64 * 1024 ** 3, 'History snapshot exceeds its limit.')
        with self._writer('sync'):
            for pending in (self.root/'sync-stages').glob('*.json'):
                ownership=json_read(pending)
                if ownership.get('root') == str(self.root) and ownership.get('phase') == 'recovery_required' and ownership.get('generationId') == generation_id:
                    raise ValueError('A verified snapshot is already preserved for explicit recovery; automatic synchronization is held.')
            stage_id = uuid.uuid4().hex
            stage = self.root / 'generations' / ('.' + stage_id)
            receipt = self.root / 'sync-stages' / (stage_id + '.json')
            # Write ownership before creation: every crash-created stage is attributable.
            json_write(receipt, dict(format='interlinked-history-sync-stage-v1', root=str(self.root),
                                     stage=stage.name, snapshotId=snapshot_id,generationId=generation_id))
            stage.mkdir()
            try:
                return self._install_snapshot_stage(manifest, reader, stage, total, cancel, progress)
            finally:
                try:
                    if receipt.exists() and json_read(receipt).get('phase') == 'recovery_required':
                        self.cleanup_pending = True
                        self.recovery_required = True
                    else: self._cleanup_sync_stage(stage, receipt)
                except (ValueError, OSError):
                    self.cleanup_pending = True

    def _install_snapshot_stage(self, manifest, reader, stage, total, cancel, progress):
        generation_id, snapshot_id = manifest['generationId'], manifest['snapshotId']
        files = manifest['files']
        transferred = 0
        for row in files:
            cancelled(cancel)
            path = _config.ordinary(stage / row['path'], missing=True)
            digest = hashlib.sha256()
            with open(path, 'xb') as stream:
                offset = 0
                while offset < row['bytes']:
                    cancelled(cancel)
                    amount = min(MAX_CHUNK, row['bytes'] - offset)
                    chunk = reader(row['path'], offset, amount)
                    need(isinstance(chunk, bytes) and len(chunk) == amount, 'Incomplete history snapshot chunk.')
                    stream.write(chunk); digest.update(chunk)
                    offset += len(chunk); transferred += len(chunk)
                    if progress:
                        progress(dict(phase='syncing', bytes=transferred, total=total))
                stream.flush(); os.fsync(stream.fileno())
            need(digest.hexdigest() == row['sha256'], 'History snapshot file hash differs.')
        info = json_read(stage / 'generation.json')
        need(info.get('format') == FORMAT and info.get('generationId') == generation_id and info.get('complete') is True,
             'Invalid snapshot generation metadata.')
        need(file_hash(stage / 'index.sqlite3') == info.get('indexSha256'), 'Snapshot generation index differs.')
        if info.get('semantic'):
            need(file_hash(stage / 'vectors.npy') == info.get('vectorsSha256'), 'Snapshot generation vectors differ.')
        self._validate_snapshot_index(stage)
        controls = json_read(stage / 'controls.json')
        self._validate_controls(controls)
        cancelled(cancel)
        target = self.root / 'generations' / generation_id
        if target.exists():
            try:
                existing = self._generation(generation_id)
                need(existing[1] == info and file_hash(target/'generation.json') == file_hash(stage/'generation.json') and
                     file_hash(target/'index.sqlite3') == info['indexSha256'], 'Existing generation content differs.')
                if info.get('semantic'):
                    need(file_hash(target/'vectors.npy') == info['vectorsSha256'], 'Existing generation vectors differ.')
            except (ValueError,OSError,KeyError,TypeError):
                receipt=self.root/'sync-stages'/(stage.name[1:]+'.json')
                ownership=json_read(receipt);ownership['phase']='recovery_required';json_write(receipt,ownership)
                raise ValueError('Retained history generation is damaged; verified incoming snapshot preserved for explicit recovery.') from None
        else:
            os.replace(stage, target)
        # Incoming authority never removes replica-local exclusions.
        with self._controls() as db:
            db.execute("DELETE FROM exclusions WHERE origin='primary'")
            for row in controls['exclusions']:
                db.execute('INSERT OR REPLACE INTO exclusions VALUES(?,?,\'primary\',?,?)', (row['scope'], row['value'], row['enabled'], row['updated']))
            db.execute('DELETE FROM memories')
            for row in controls['memories']:
                db.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?,?)', tuple(row[key] for key in ('id', 'text', 'category', 'project', 'status', 'refs', 'created', 'updated')))
            db.execute('DELETE FROM project_assignments')
            for row in controls.get('projects', []):
                db.execute('INSERT INTO project_assignments VALUES(?,?,?,?)', tuple(row[key] for key in ('scope', 'value', 'project', 'updated')))
        json_write(self.root / 'ACTIVE.json', dict(generationId=generation_id, activatedAt=utc(), snapshotId=snapshot_id, syncedAt=utc()))
        with self._controls() as db:
            db.execute('INSERT OR REPLACE INTO activations VALUES(?,?)', (generation_id, utc()))
        return self.status()

    @staticmethod
    def _validate_snapshot_index(stage):
        path = _config.ordinary(stage / 'index.sqlite3')
        db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            need(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'Snapshot SQLite verification failed.')
            objects = db.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            allowed = {'records', 'heads', 'vector_ids', 'chunks', 'restrictions', 'fts', 'fts_data', 'fts_idx', 'fts_content', 'fts_docsize', 'fts_config'}
            need(all(name in allowed and kind == 'table' for name, kind in objects), 'Unexpected snapshot database objects.')
            need({'records', 'heads', 'vector_ids', 'chunks', 'fts'} <= {name for name, _ in objects}, 'Snapshot database tables are incomplete.')
            db.execute('SELECT id,revision,kind,title,text,timestamp,project,memory_allowed,metadata FROM records LIMIT 1').fetchall()
            db.execute('SELECT id,revision,seen FROM heads LIMIT 1').fetchall()
        finally:
            db.close()

    @staticmethod
    def _validate_controls(controls):
        need(isinstance(controls, dict) and {'exclusions', 'memories'} <= set(controls) <= {'exclusions', 'memories', 'projects'} and all(isinstance(controls[key], list) for key in controls), 'Invalid snapshot controls.')
        for row in controls['exclusions']:
            need(row.get('scope') in ('record', 'conversation', 'asset') and isinstance(row.get('value'), str) and 0 < len(row['value']) <= 500 and row.get('enabled') in (0, 1) and isinstance(row.get('updated'), str), 'Invalid snapshot exclusion.')
        for row in controls['memories']:
            need(isinstance(row.get('id'), str) and GEN.fullmatch(row['id']) and row.get('status') in ('proposed', 'approved', 'rejected') and isinstance(row.get('text'), str) and len(row['text'].encode('utf-8')) <= 16000, 'Invalid snapshot memory.')
            refs = json.loads(row.get('refs', 'null'))
            need(isinstance(refs, list) and 1 <= len(refs) <= 30 and all(isinstance(ref, dict) and HEX.fullmatch(ref.get('id', '')) and HEX.fullmatch(ref.get('revision', '')) for ref in refs), 'Invalid snapshot memory sources.')
        for row in controls.get('projects', []):
            need(row.get('scope') in ('record', 'conversation', 'asset') and isinstance(row.get('value'), str) and 0 < len(row['value']) <= 500 and isinstance(row.get('project'), str) and len(row['project'].encode('utf-8')) <= 200 and isinstance(row.get('updated'), str), 'Invalid snapshot project assignment.')

    def close(self):
        """No retained SQLite handles or background inference owners."""
        return None
