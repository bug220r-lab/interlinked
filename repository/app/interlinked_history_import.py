"""Passive, verified ChatGPT export intake and text adapters.

No network requests, application execution, model inference or production writes.
Callers own activation, exclusion policy and the private destination directory.
Only aggregate counters belong in progress callbacks or cloud-visible logs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
import datetime as dt
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET


_spec = importlib.util.spec_from_file_location('history_import_configuration', Path(__file__).with_name('interlinked_configuration.py'))
_files = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_files)
ordinary = _files.ordinary


def _load_dependencies():
    # Isolated PDF child processes do not inherit their parent's sys.path.
    # Load the exact sibling helper, whose manifest pins every dependency byte.
    runtime_path = Path(__file__).with_name('interlinked_history_runtime.py')
    if runtime_path.exists():
        sys.modules.setdefault('interlinked_configuration', _files)
        runtime_spec = importlib.util.spec_from_file_location('history_import_runtime', runtime_path)
        runtime = importlib.util.module_from_spec(runtime_spec)
        runtime_spec.loader.exec_module(runtime)
        runtime.load_dependencies()


_load_dependencies()
FORMAT = 'interlinked-history-intake-v1'
ADAPTER_VERSION = 'chatgpt-export-v1.0'
MANIFEST = 'export_manifest.json'
MAX_METADATA = 64 * 1024 * 1024
MAX_RECORD = 64 * 1024 * 1024
MAX_TEXT = 32 * 1024 * 1024
MAX_FILES = 100000
MAX_TOTAL = 100 * 1024 * 1024 * 1024
MAX_MEMBER = 512 * 1024 * 1024
MAX_ARCHIVE_EXPANSION = 1024 * 1024 * 1024
BLOCK = 1024 * 1024
TEXT_EXTENSIONS = {'.txt', '.md', '.markdown', '.rst', '.csv', '.tsv', '.json', '.jsonl', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.log', '.sql', '.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.htm', '.xml', '.ps1', '.psm1', '.sh', '.bat', '.c', '.cpp', '.h', '.cs', '.rs', '.go', '.java', '.vue', '.svelte'}
DEVICE = re.compile(r'^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9]|LPT[1-9])(?:\..*)?$', re.I)
FILE_ID = re.compile(r'file[_-][A-Za-z0-9]+')


class Cancelled(ValueError):
    pass


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled('History import cancelled; its checkpoint was retained.')


def _need(value, message):
    if not value:
        raise ValueError(message)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def _atomic(path, value):
    path = ordinary(Path(path).absolute(), missing=True)
    temporary = path.with_name('.history-' + uuid.uuid4().hex + '.tmp')
    with open(_files.fs(temporary), 'xb') as stream:
        stream.write(_encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(_files.fs(temporary), _files.fs(path))


def safe_relative(value):
    _need(isinstance(value, str) and 0 < len(value) <= 2000 and '\\' not in value and ':' not in value and not any(ord(c) < 32 for c in value), 'Unsafe export relative path.')
    path = PurePosixPath(value)
    _need(not path.is_absolute() and path.as_posix() == value and all(p not in ('', '.', '..') and not p.endswith((' ', '.')) and not DEVICE.fullmatch(p) for p in path.parts), 'Unsafe export relative path.')
    return path


def _source_path(path):
    """Allow Cloud Files tags only at the explicit intake boundary."""
    path = Path(path).absolute()
    _need(not str(path).startswith('\\\\') and ':' not in str(path)[len(path.anchor):], 'Intake requires a local absolute path.')
    for item in reversed((path, *path.parents)):
        info = os.lstat(_files.fs(item))
        tag = getattr(info, 'st_reparse_tag', 0)
        attributes = getattr(info, 'st_file_attributes', 0)
        _need(not stat.S_ISLNK(info.st_mode), 'Symbolic links are refused during intake.')
        if attributes & 0x400:
            _need(tag and (tag & 0xFFFF0FFF) == 0x9000001A, 'Unknown or redirecting reparse point refused during intake.')
        if stat.S_ISREG(info.st_mode):
            _need(info.st_nlink == 1, 'Shared source files are refused during intake.')
    return path


def _walk_source(source):
    # Extended Win32 paths are an I/O detail, never logical receipt identities.
    extended = Path(_files.fs(source))
    for directory, dirs, names in os.walk(extended, followlinks=False):
        yield source / Path(directory).relative_to(extended), dirs, names


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


@contextmanager
def _open_source(path):
    path = _source_path(path)
    before = os.stat(_files.fs(path))
    _need(stat.S_ISREG(before.st_mode), 'Intake source must be a regular file.')
    if os.name == 'nt':
        import ctypes
        import msvcrt
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        handle = kernel.CreateFileW(_files.fs(path), 0x80000000, 1, None, 3, 0x08000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
            raise
        stream = os.fdopen(descriptor, 'rb')
    else:
        stream = os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb')
    with stream:
        actual = os.fstat(stream.fileno())
        _need(_identity(actual) == _identity(before) and actual.st_nlink == 1, 'Source identity changed before intake.')
        try:
            yield stream
        finally:
            after = os.fstat(stream.fileno())
            _source_path(path)
            _need(_identity(after) == _identity(actual) == _identity(os.stat(_files.fs(path))), 'Source changed during intake.')


def _read_source(path, limit=MAX_METADATA):
    with _open_source(path) as stream:
        value = stream.read(limit + 1)
    _need(len(value) <= limit, 'Export metadata exceeds its limit.')
    return value


def _file_digest(path, cancel=None):
    result = hashlib.sha256()
    size = 0
    with open(_files.fs(ordinary(Path(path))), 'rb') as stream:
        while True:
            _check(cancel)
            block = stream.read(BLOCK)
            if not block:
                break
            size += len(block)
            result.update(block)
    return result.hexdigest(), size


def _manifest(data):
    _need(isinstance(data, dict) and data.get('version') == 1 and isinstance(data.get('export_files'), list) and isinstance(data.get('logical_files'), dict), 'Unsupported export manifest format.')
    rows = data['export_files']
    _need(len(rows) <= MAX_FILES, 'Export file count exceeds the intake limit.')
    seen, total, entries = set(), 0, []
    for row in rows:
        _need(isinstance(row, dict) and isinstance(row.get('size_bytes'), int) and not isinstance(row.get('size_bytes'), bool) and 0 <= row['size_bytes'] <= MAX_TOTAL, 'Invalid export file size.')
        rel = safe_relative(row.get('path')).as_posix()
        _need(rel.casefold() not in seen and rel != MANIFEST, 'Duplicate, case-colliding or reserved manifest path.')
        seen.add(rel.casefold())
        total += row['size_bytes']
        _need(total <= MAX_TOTAL, 'Export exceeds the intake size limit.')
        entries.append(dict(path=rel, size_bytes=row['size_bytes']))
    for logical in data['logical_files'].values():
        _need(isinstance(logical, dict) and isinstance(logical.get('files'), list), 'Invalid logical export file map.')
        for rel in logical['files']:
            _need(safe_relative(rel).as_posix().casefold() in seen, 'Logical export map references an unlisted payload.')
        if logical.get('sharded'):
            _need(logical.get('shard_count') == len(logical['files']), 'Export shard count differs.')
    return entries


def _zip_members(archive, expansion=MAX_TOTAL):
    entries, total = {}, 0
    for info in archive.infolist():
        if info.is_dir():
            safe_relative(info.filename.rstrip('/'))
            continue
        rel = safe_relative(info.filename).as_posix()
        _need(rel.casefold() not in entries, 'Duplicate or case-colliding archive member.')
        mode = info.external_attr >> 16
        _need(not stat.S_ISLNK(mode) and not info.flag_bits & 1, 'Linked or encrypted archive member refused.')
        _need(info.file_size <= MAX_TOTAL and (info.file_size < BLOCK or info.file_size <= max(1, info.compress_size) * 1000), 'Archive expansion ratio exceeds the intake limit.')
        total += info.file_size
        _need(total <= expansion and len(entries) < MAX_FILES, 'Archive expansion exceeds its limit.')
        entries[rel.casefold()] = info
    return entries


def source_identity(source, cancel=None):
    """Return the guarded manifest digest used to choose a distinct intake owner.

    This is a preview identity only: stage_export independently revalidates the
    manifest, payload inventory and source identities while copying all bytes.
    """
    _check(cancel)
    source = _source_path(Path(source).absolute())
    if os.path.isdir(_files.fs(source)):
        raw = _read_source(source / MANIFEST)
    else:
        with _open_source(source) as stream, zipfile.ZipFile(stream) as archive:
            members = _zip_members(archive)
            _need(MANIFEST in members and members[MANIFEST].file_size <= MAX_METADATA, 'ZIP manifest is missing or exceeds its limit.')
            raw = archive.read(members[MANIFEST])
    _manifest(json.loads(raw.decode('utf-8-sig')))
    _check(cancel)
    return _hash(raw)


def stage_export(source, destination, expected_manifest_sha256=None, cancel=None, progress=None):
    """Verify a folder/ZIP into owned, ordinary storage; resume by rehashing completed files.

    The returned stagedRoot is the payload directory accepted by iter_records().
    Existing files are reused only when both the checkpoint and their fresh digest
    match. Source files are always freshly read, including a resumed intake.
    """
    source = _source_path(Path(source).absolute())
    destination = ordinary(Path(destination).absolute(), missing=True)
    _need(destination != source and not destination.is_relative_to(source) and not source.is_relative_to(destination), 'Intake destination must be separate from its source.')
    os.makedirs(_files.fs(destination), exist_ok=True)
    ordinary(destination)
    owner_path = destination / 'OWNER.json'
    owner = dict(format=FORMAT, source=str(source), root=str(destination))
    if os.path.exists(_files.fs(owner_path)):
        _need(json.loads(_files.read_bytes(owner_path)) == owner, 'Intake directory has a different owner.')
    else:
        _need(not any(os.scandir(_files.fs(destination))), 'Intake directory must be empty or owned by this import.')
        _atomic(owner_path, owner)
    payload = destination / 'source'
    os.makedirs(_files.fs(payload), exist_ok=True)
    ordinary(payload)
    checkpoint_path = destination / 'CHECKPOINT.json'
    checkpoint = json.loads(_files.read_bytes(checkpoint_path, MAX_METADATA)) if os.path.exists(_files.fs(checkpoint_path)) else dict(format=FORMAT, completed={})
    _need(checkpoint.get('format') == FORMAT and isinstance(checkpoint.get('completed'), dict), 'Invalid intake checkpoint.')

    @contextmanager
    def input_files():
        if os.path.isdir(_files.fs(source)):
            raw = _read_source(source / MANIFEST)
            manifest = json.loads(raw.decode('utf-8-sig'))
            rows = _manifest(manifest)
            actual = set()
            for directory, dirs, names in _walk_source(source):
                _source_path(directory)
                for name in dirs:
                    _source_path(Path(directory) / name)
                for name in names:
                    p = _source_path(Path(directory) / name)
                    actual.add(p.relative_to(source).as_posix())
                    _need(len(actual) <= MAX_FILES + 1, 'Source traversal exceeds its limit.')
            _need(actual == {r['path'] for r in rows} | {MANIFEST}, 'Export file inventory differs from its manifest.')
            yield raw, manifest, rows, lambda rel: _open_source(source.joinpath(*safe_relative(rel).parts))
        else:
            with _open_source(source) as stream, zipfile.ZipFile(stream) as archive:
                members = _zip_members(archive)
                _need(MANIFEST in members, 'ZIP must contain its export manifest at the root.')
                _need(members[MANIFEST].file_size <= MAX_METADATA, 'Export manifest exceeds its limit.')
                raw = archive.read(members[MANIFEST])
                manifest = json.loads(raw.decode('utf-8-sig'))
                rows = _manifest(manifest)
                _need(set(members) == {r['path'].casefold() for r in rows} | {MANIFEST}, 'ZIP inventory differs from its manifest.')
                for row in rows:
                    _need(members[row['path'].casefold()].file_size == row['size_bytes'], 'ZIP member size differs from manifest.')
                yield raw, manifest, rows, lambda rel: archive.open(members[rel.casefold()])

    with input_files() as (raw, manifest, rows, opener):
        manifest_hash = _hash(raw)
        _need(expected_manifest_sha256 is None or manifest_hash == expected_manifest_sha256, 'Export manifest fingerprint changed.')
        _need(not checkpoint.get('manifestSha256') or checkpoint['manifestSha256'] == manifest_hash, 'Resume requires the same export manifest.')
        checkpoint['manifestSha256'] = manifest_hash
        _atomic(checkpoint_path, checkpoint)
        all_rows = [dict(path=MANIFEST, size_bytes=len(raw))] + rows
        total_bytes, receipts = 0, []
        for index, row in enumerate(all_rows):
            _check(cancel)
            rel = row['path']
            target = ordinary(payload.joinpath(*safe_relative(rel).parts), missing=True)
            os.makedirs(_files.fs(target.parent), exist_ok=True)
            ordinary(target.parent)
            temporary = target.with_name('.intake-' + uuid.uuid4().hex + '.tmp')
            digest, count = hashlib.sha256(), 0
            before = None
            try:
                with opener(rel) as input_stream, open(_files.fs(temporary), 'xb') as output:
                    if hasattr(input_stream, 'fileno'):
                        try:
                            info = os.fstat(input_stream.fileno())
                            before = dict(volume=info.st_dev, fileId=info.st_ino, bytes=info.st_size, modifiedNs=info.st_mtime_ns)
                        except (OSError, io.UnsupportedOperation):
                            pass
                    while True:
                        _check(cancel)
                        block = input_stream.read(BLOCK)
                        if not block:
                            break
                        count += len(block)
                        _need(count <= row['size_bytes'], 'Source grew beyond its manifest size.')
                        digest.update(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                _need(count == row['size_bytes'], 'Source size differs from its manifest.')
                sha = digest.hexdigest()
                staged_hash, staged_size = _file_digest(temporary, cancel)
                _need((sha, count) == (staged_hash, staged_size), 'Intake copy verification failed.')
                if os.path.exists(_files.fs(target)):
                    existing = _file_digest(target, cancel)
                    prior = checkpoint['completed'].get(rel)
                    _need((prior is None or prior.get('sha256') == sha) and existing == (sha, count), 'Existing staged file differs; use a new intake directory.')
                    os.unlink(_files.fs(temporary))
                else:
                    os.replace(_files.fs(temporary), _files.fs(target))
                receipt = dict(path=rel, bytes=count, sha256=sha, sourceIdentity=before)
                checkpoint['completed'][rel] = receipt
                _atomic(checkpoint_path, checkpoint)
                receipts.append(receipt)
                total_bytes += count
                if progress:
                    progress(dict(phase='intake', completed=index + 1, total=len(all_rows), bytes=total_bytes))
            finally:
                if os.path.exists(_files.fs(temporary)):
                    os.unlink(_files.fs(ordinary(temporary)))
        if os.path.isdir(_files.fs(source)):
            # A source copied early must not change while later files are copied.
            for row in receipts:
                _check(cancel)
                info = os.stat(_files.fs(_source_path(source.joinpath(*safe_relative(row['path']).parts))))
                identity = row['sourceIdentity']
                _need(identity is not None and _identity(info) == (identity['volume'], identity['fileId'], identity['bytes'], identity['modifiedNs']), 'Source changed before intake completed.')
            current_paths = set()
            for directory, dirs, names in _walk_source(source):
                _source_path(directory)
                for name in dirs:
                    _source_path(Path(directory) / name)
                for name in names:
                    current_paths.add(_source_path(Path(directory) / name).relative_to(source).as_posix())
                    _need(len(current_paths) <= MAX_FILES + 1, 'Source traversal exceeds its limit.')
            _need(current_paths == {row['path'] for row in receipts}, 'Source inventory changed before intake completed.')
    result = dict(format=FORMAT, adapterVersion=ADAPTER_VERSION, stagedRoot=str(payload), source=str(source), manifestSha256=manifest_hash,
                  files=len(receipts), bytes=total_bytes, entries=receipts, complete=True)
    _atomic(destination / 'RECEIPT.json', result)
    return result


def _read_json(path, limit=MAX_METADATA):
    return json.loads(_files.read_bytes(ordinary(Path(path)), limit).decode('utf-8-sig'))


def _iter_array(path):
    """Streaming top-level JSON array with a bounded individual record."""
    path = ordinary(Path(path))
    decoder = json.JSONDecoder()
    with open(_files.fs(path), 'r', encoding='utf-8-sig') as stream:
        buffer, ended, started, expect_value, after_comma = '', False, False, True, False
        while True:
            if not buffer.strip() and not ended:
                chunk = stream.read(65536)
                buffer += chunk
                ended = not chunk
            buffer = buffer.lstrip()
            if not started:
                _need(buffer.startswith('['), 'Expected an export JSON array.')
                buffer, started = buffer[1:].lstrip(), True
            if buffer.startswith(']'):
                _need(not after_comma, 'Trailing JSON array comma.')
                _need(not (buffer[1:] + stream.read()).strip(), 'Unexpected content after export array.')
                return
            if not expect_value:
                if not buffer and not ended:
                    chunk = stream.read(65536); buffer += chunk; ended = not chunk
                    continue
                _need(buffer.startswith(','), 'Missing export array separator.')
                buffer, expect_value, after_comma = buffer[1:].lstrip(), True, True
                continue
            try:
                value, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                _need(not ended, 'Truncated or malformed export JSON.')
                chunk = stream.read(65536)
                buffer += chunk; ended = not chunk
                _need(len(buffer.encode('utf-8')) <= MAX_RECORD, 'Individual export record exceeds its limit.')
                continue
            yield value
            buffer, expect_value, after_comma = buffer[end:].lstrip(), False, False


def _timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.astimezone(dt.timezone.utc).isoformat() if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc).isoformat()
        except ValueError:
            pass
    return None


def _record(logical_id, kind, title, text, timestamp=None, memory_allowed=False, metadata=None, restricted_source_ids=()):
    text = str(text).replace('\0', '').strip()
    data = dict(id=_hash(logical_id), kind=kind, title=str(title or kind)[:1000], text=text, timestamp=_timestamp(timestamp), project='',
                memory_allowed=bool(memory_allowed), metadata=metadata or {}, restricted_source_ids=sorted(set(restricted_source_ids)))
    data['revision'] = _hash(_encoded(data))
    return data


def _issues(coverage, code, count=1):
    coverage.setdefault('issues', {})[code] = coverage.setdefault('issues', {}).get(code, 0) + count


def _count(coverage, key, count=1):
    coverage[key] = coverage.get(key, 0) + count


def _references(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)
    elif isinstance(value, str):
        yield from FILE_ID.findall(value)


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.result, self.suppressed = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.suppressed += 1
        elif tag in ('p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3'):
            self.result.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.suppressed = max(0, self.suppressed - 1)

    def handle_data(self, data):
        if not self.suppressed:
            self.result.append(data)


def _xml(data):
    declarations = data.upper().replace(b'\0', b'')
    _need(len(data) <= MAX_TEXT and b'<!DOCTYPE' not in declarations and b'<!ENTITY' not in declarations, 'Active or oversized XML refused.')
    return ET.fromstring(data)


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _zip_read(archive, name, limit=MAX_TEXT):
    info = archive.getinfo(name)
    _need(info.file_size <= limit, 'Document member exceeds its limit.')
    return archive.read(info)


def _xml_elements(archive, name, selected):
    """Stream large worksheets without retaining prior rows or active XML.

    The archive's expansion limits still apply. A selected element and any
    uninterrupted XML construct remain bounded by MAX_TEXT, even when the
    complete sheet is larger. Removing completed children bounds tree memory.
    """
    info = archive.getinfo(name)
    _need(info.file_size <= MAX_MEMBER, 'Document member exceeds its limit.')
    parser = ET.XMLPullParser(events=('start', 'end'))
    stack, active, active_bytes, active_nodes, pending_bytes, tail = [], None, 0, 0, 0, b''
    with archive.open(info) as stream:
        while True:
            data = stream.read(min(65536, max(1, MAX_TEXT // 4)))
            if not data:
                break
            declarations = (tail + data).upper().replace(b'\0', b'')
            _need(b'<!DOCTYPE' not in declarations and b'<!ENTITY' not in declarations, 'Active or oversized XML refused.')
            tail = data[-80:]
            active_bytes += len(data) if active is not None else 0
            pending_bytes += len(data)
            _need(active_bytes <= MAX_TEXT and pending_bytes <= MAX_TEXT, 'Individual XML record exceeds its limit.')
            parser.feed(data)
            for event, element in parser.read_events():
                pending_bytes = 0
                if event == 'start':
                    stack.append(element)
                    if active is None and _local(element.tag) == selected:
                        active, active_bytes, active_nodes = element, len(data), 0
                    if active is not None:
                        active_nodes += 1
                        _need(active_nodes <= 100000, 'Individual XML record exceeds its node limit.')
                else:
                    if element is active:
                        _need(len(ET.tostring(element, encoding='utf-8')) <= MAX_TEXT, 'Individual XML record exceeds its limit.')
                        yield element
                        active, active_bytes = None, 0
                    if active is None:
                        if len(stack) > 1:
                            stack[-2].remove(element)
                        element.clear()
                    stack.pop()
    parser.close()


def _ooxml(archive, kind):
    # Relationships are inspected as data, never fetched. Package types must agree.
    types = _xml(_zip_read(archive, '[Content_Types].xml'))
    accepted = {'docx': 'wordprocessingml.document.main+xml', 'xlsx': 'spreadsheetml.sheet.main+xml', 'pptx': 'presentationml.presentation.main+xml'}[kind]
    _need(any(accepted in item.attrib.get('ContentType', '') for item in types), 'OOXML content type differs from package structure.')
    if kind == 'docx':
        names = ['word/document.xml'] + sorted(n for n in archive.namelist() if re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml', n))
        for name in names:
            root = _xml(_zip_read(archive, name))
            paragraphs = [''.join(n.text or '' for n in p.iter() if _local(n.tag) == 't') for p in root.iter() if _local(p.tag) == 'p']
            yield name, '\n'.join(paragraphs), dict(part=name)
    elif kind == 'pptx':
        names = sorted((n for n in archive.namelist() if re.fullmatch(r'ppt/(?:slides/slide|notesSlides/notesSlide)\d+\.xml', n)), key=lambda n: (n.split('/')[1], int(re.search(r'(\d+)\.xml$', n).group(1))))
        for name in names:
            root = _xml(_zip_read(archive, name))
            yield name, '\n'.join(n.text or '' for n in root.iter() if _local(n.tag) == 't'), dict(part=name, slide=int(re.search(r'(\d+)\.xml$', name).group(1)), notes='notesSlides' in name)
    else:
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared_bytes = 0
            for item in _xml_elements(archive, 'xl/sharedStrings.xml', 'si'):
                value = ''.join(n.text or '' for n in item.iter() if _local(n.tag) == 't')
                shared_bytes += len(value.encode('utf-8'))
                _need(shared_bytes <= MAX_TEXT and len(shared) < 1000000, 'Spreadsheet shared-string table exceeds its limit.')
                shared.append(value)
        names = {}
        workbook = _xml(_zip_read(archive, 'xl/workbook.xml'))
        relationships = _xml(_zip_read(archive, 'xl/_rels/workbook.xml.rels')) if 'xl/_rels/workbook.xml.rels' in archive.namelist() else []
        targets = {x.attrib.get('Id'): x.attrib.get('Target') for x in relationships if x.attrib.get('TargetMode') != 'External'}
        for sheet in workbook.iter():
            if _local(sheet.tag) == 'sheet':
                relid = next((v for k, v in sheet.attrib.items() if _local(k) == 'id'), None)
                target = targets.get(relid, '')
                if target:
                    part = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                    safe_relative(part)
                    names[part] = sheet.attrib.get('name', 'Sheet')
        for name in sorted(n for n in archive.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)):
            for row in _xml_elements(archive, name, 'row'):
                values, cells = [], []
                for cell in row:
                    if _local(cell.tag) != 'c':
                        continue
                    address = cell.attrib.get('r', '')
                    value = next((x.text or '' for x in cell if _local(x.tag) == 'v'), '')
                    formula = next((x.text or '' for x in cell if _local(x.tag) == 'f'), None)
                    if cell.attrib.get('t') == 's':
                        try:
                            value = shared[int(value)]
                        except (ValueError, IndexError):
                            value = '[unavailable shared string]'
                    elif cell.attrib.get('t') == 'inlineStr':
                        value = ''.join(n.text or '' for n in cell.iter() if _local(n.tag) == 't')
                    if formula is not None:
                        value = 'formula: =' + formula + '; cached value: ' + value
                    if not value.strip():
                        continue
                    values.append(address + ': ' + value); cells.append(address)
                if values:
                    yield name + ':row:' + row.attrib.get('r', ''), '\n'.join(values), dict(part=name, sheet=names.get(name, name), row=row.attrib.get('r'), cells=cells)


def _pdf_worker(source, destination):
    """Private subprocess entry point. Text is written only to its private output."""
    _restrict_worker_memory(768 * 1024 * 1024)
    from pypdf import PdfReader
    reader = PdfReader(str(source))
    if reader.is_encrypted:
        return 3
    _need(len(reader.pages) <= 20000, 'PDF page count exceeds its limit.')
    total = 0
    with open(destination, 'xb') as output:
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ''
            record = _encoded(dict(page=index + 1, text=text)) + b'\n'
            total += len(record)
            _need(total <= MAX_TEXT, 'PDF extracted text exceeds its limit.')
            output.write(record)
            output.flush()
    return 0


def _restrict_worker_memory(maximum):
    """A parser failure is preferable to an unbounded decompression allocation."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong), ('flags', wintypes.DWORD),
                        ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel.CreateJobObjectW.restype = ctypes.c_void_p
        kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel.CreateJobObjectW(None, None)
        limits = Extended(); limits.basic.flags = 0x100; limits.process_memory = maximum
        if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
        # The worker intentionally retains this handle until process exit.
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))


def _pdf(data, coverage, cancel=None):
    if importlib.util.find_spec('pypdf') is None:
        _issues(coverage, 'pdf_dependency_unavailable'); return
    with tempfile.TemporaryDirectory(prefix='interlinked-history-pdf-') as directory:
        temp = Path(directory)
        source, output, errors = temp / 'source.pdf', temp / 'text.jsonl', temp / 'errors.log'
        source.write_bytes(data)
        with open(errors, 'wb') as stderr:
            process = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).absolute()), '--extract-pdf', str(source), str(output)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 60
            reason = None
            try:
                while process.poll() is None:
                    _check(cancel)
                    if time.monotonic() >= deadline:
                        reason = 'pdf_extraction_timeout'; break
                    if (output.exists() and output.stat().st_size > MAX_TEXT) or errors.stat().st_size > BLOCK:
                        reason = 'pdf_extraction_output_limit'; break
                    time.sleep(.05)
                if reason:
                    process.kill(); process.wait(10); _issues(coverage, reason); return
                if process.returncode == 3:
                    _issues(coverage, 'encrypted_pdf'); return
                if process.returncode != 0:
                    _issues(coverage, 'pdf_extraction_failed'); return
                with open(output, 'r', encoding='utf-8') as result:
                    for line in result:
                        page = json.loads(line)
                        if page['text'].strip():
                            yield 'page:' + str(page['page']), page['text'], dict(page=page['page'])
                        else:
                            _issues(coverage, 'pdf_page_without_text')
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(10)


def _document(data, filename, coverage, depth=0, cancel=None):
    """Yield passive text segments and their exact location metadata."""
    _need(len(data) <= MAX_MEMBER, 'Attachment exceeds extraction size limit.')
    extension = PurePosixPath(filename).suffix.lower()
    if data.startswith(b'%PDF-'):
        yield from _pdf(data, coverage, cancel)
        return
    if data.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = _zip_members(archive, MAX_ARCHIVE_EXPANSION)
                names = set(archive.namelist())
                kind = 'docx' if 'word/document.xml' in names else 'xlsx' if 'xl/workbook.xml' in names else 'pptx' if 'ppt/presentation.xml' in names else None
                if kind:
                    yield from _ooxml(archive, kind)
                elif depth < 2:
                    for info in members.values():
                        _check(cancel)
                        if info.file_size > MAX_TEXT:
                            _issues(coverage, 'archive_member_too_large'); continue
                        for part, text, meta in _document(archive.read(info), info.filename, coverage, depth + 1, cancel):
                            yield info.filename + '/' + part, text, dict(meta, member=info.filename)
                else:
                    _issues(coverage, 'nested_archive_limit')
        except Cancelled:
            raise
        except (ValueError, zipfile.BadZipFile, ET.ParseError, KeyError, RuntimeError):
            _issues(coverage, 'invalid_document_container')
        return
    if data.startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF87a', b'GIF89a')) or b'\0' in data[:4096]:
        _issues(coverage, 'media_or_binary_preserved'); return
    if len(data) > MAX_TEXT:
        _issues(coverage, 'text_document_too_large'); return
    if extension not in TEXT_EXTENSIONS and extension not in ('', '.dat'):
        _issues(coverage, 'unsupported_document_type'); return
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        _issues(coverage, 'unsupported_text_encoding'); return
    if any(ord(c) < 32 and c not in '\r\n\t' for c in text):
        _issues(coverage, 'binary_text_candidate'); return
    if extension in ('.html', '.htm') or text.lstrip().lower().startswith(('<!doctype html', '<html')):
        parser = _HTML(); parser.feed(text); text = ''.join(parser.result)
    if text.strip():
        yield 'text', text, dict(part='text')


def _git_bundle(path, coverage, cancel):
    """Read bounded blobs through a temporary object store; never check out files."""
    git = shutil.which('git')
    if not git:
        _issues(coverage, 'git_dependency_unavailable'); return
    with tempfile.TemporaryDirectory(prefix='interlinked-history-git-') as directory:
        temp = Path(directory)
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1')
        for key in list(env):
            if key.startswith(('GIT_DIR', 'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')):
                env.pop(key, None)
        def run(args, limit=MAX_TEXT):
            _check(cancel)
            result = subprocess.run([git, '-c', 'core.longpaths=true', '-c', 'core.hooksPath=' + os.devnull, '-c', 'protocol.file.allow=always', *args], cwd=temp, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            _need(result.returncode == 0 and len(result.stdout) <= limit, 'Site bundle operation failed or exceeded its limit.')
            return result.stdout
        try:
            run(['init', '--bare', '--quiet', '.'])
            heads = run(['bundle', 'unbundle', str(path)]).decode('utf-8').splitlines()
            commits = []
            for line in heads:
                parts = line.split(' ', 1)
                if re.fullmatch('[a-f0-9]{40,64}', parts[0]) and parts[0] not in commits:
                    commits.append(parts[0])
            _need(len(commits) <= 1000, 'Site bundle reference count exceeds its limit.')
            seen = set()
            for commit in commits:
                for entry in run(['ls-tree', '-r', '-z', commit]).split(b'\0'):
                    if not entry:
                        continue
                    descriptor, name = entry.split(b'\t', 1)
                    mode, kind, object_id = descriptor.decode('ascii').split()
                    rel = name.decode('utf-8')
                    safe_relative(rel)
                    if kind != 'blob' or mode not in ('100644', '100755') or (rel, object_id) in seen:
                        continue
                    seen.add((rel, object_id))
                    if PurePosixPath(rel).suffix.lower() not in TEXT_EXTENSIONS:
                        _issues(coverage, 'site_blob_not_text'); continue
                    size = int(run(['cat-file', '-s', object_id], 100))
                    if size > MAX_TEXT:
                        _issues(coverage, 'site_blob_too_large'); continue
                    data = run(['cat-file', 'blob', object_id])
                    for part, text, meta in _document(data, rel, coverage, cancel=cancel):
                        yield commit + ':' + rel + ':' + part, text, dict(meta, commit=commit, gitPath=rel, blob=object_id)
        except Cancelled:
            raise
        except (ValueError, subprocess.SubprocessError, UnicodeError):
            _issues(coverage, 'site_bundle_unreadable')


def iter_records(stage_root, coverage, cancel=None):
    """Yield content-bearing records from a verified stage. No content is logged."""
    root = ordinary(Path(stage_root).absolute())
    _need(isinstance(coverage, dict), 'Mutable aggregate coverage is required.')
    manifest = _read_json(root / MANIFEST)
    entries = _manifest(manifest)
    # Parsing arbitrary folders would bypass intake verification.
    receipt = _read_json(root.parent / 'RECEIPT.json')
    _need(receipt.get('format') == FORMAT and receipt.get('complete') is True and receipt.get('stagedRoot') == str(root), 'Verified intake receipt is required.')
    _need(_file_digest(root / MANIFEST, cancel)[0] == receipt.get('manifestSha256'), 'Staged manifest changed.')
    receipts = {row['path']: row for row in receipt.get('entries', [])}
    for row in entries:
        _check(cancel)
        item = root.joinpath(*safe_relative(row['path']).parts)
        prior = receipts.get(row['path'], {})
        _need(_file_digest(item, cancel) == (prior.get('sha256'), row['size_bytes']), 'Staged payload changed after verification.')
    coverage.update(adapterVersion=ADAPTER_VERSION, payload_files=len(entries), source_bytes=sum(row['size_bytes'] for row in entries))
    logical = manifest['logical_files'].get('conversations.json', {})
    shards = logical.get('files', [])
    restrictions, associations = defaultdict(set), defaultdict(set)
    seen_conversations = set()
    for shard in shards:
        for conversation in _iter_array(root.joinpath(*safe_relative(shard).parts)):
            _check(cancel)
            _need(isinstance(conversation, dict) and isinstance(conversation.get('mapping'), dict), 'Malformed conversation record.')
            cid = str(conversation.get('id') or conversation.get('conversation_id') or '')
            _need(cid and cid not in seen_conversations, 'Missing or duplicate conversation identity.')
            seen_conversations.add(cid)
            mapping = conversation['mapping']
            _need(all(isinstance(node, dict) for node in mapping.values()), 'Malformed conversation graph node.')
            completed = set()
            for origin in mapping:
                chain, node_id = set(), origin
                while node_id in mapping and node_id not in completed:
                    _need(node_id not in chain, 'Conversation parent graph contains a cycle.')
                    chain.add(node_id)
                    node_id = mapping[node_id].get('parent')
                completed.update(chain)
            restricted = conversation.get('is_do_not_remember') is True
            restriction = _hash('conversation:' + cid)
            _count(coverage, 'conversations'); _count(coverage, 'mapping_nodes', len(mapping))
            if restricted:
                _count(coverage, 'do_not_remember_conversations')
            children = Counter(str(node.get('parent')) for node in mapping.values() if isinstance(node, dict) and node.get('parent'))
            forks = sum(v > 1 for v in children.values())
            _count(coverage, 'branch_points', forks); _count(coverage, 'branched_conversations', bool(forks))
            current, active, visiting = conversation.get('current_node'), set(), set()
            while current is not None and current in mapping:
                _need(current not in visiting, 'Conversation parent graph contains a cycle.')
                visiting.add(current); active.add(current); current = mapping[current].get('parent')
            if any(parent not in mapping for parent in children):
                _issues(coverage, 'missing_conversation_parent')
            for node_id, node in mapping.items():
                _check(cancel)
                if not isinstance(node, dict) or not isinstance(node.get('message'), dict):
                    continue
                message = node['message']; _count(coverage, 'message_records')
                for file_id in set(_references(message)):
                    associations[file_id].add(cid)
                    if restricted:
                        restrictions[file_id].add(restriction)
                content = message.get('content') or {}
                if content.get('content_type') not in ('text', 'multimodal_text'):
                    _count(coverage, 'archival_message_records'); continue
                _count(coverage, 'readable_message_records')
                text = '\n'.join(part for part in content.get('parts', []) if isinstance(part, str))
                role = (message.get('author') or {}).get('role', 'unspecified')
                mid = str(message.get('id') or node_id)
                meta = dict(path=shard, conversationId=cid, messageId=mid, nodeId=str(node_id), parentId=node.get('parent'), currentNode=conversation.get('current_node'), onCurrentBranch=node_id in active, role=role,
                            isArchived=bool(conversation.get('is_archived')), doNotRemember=restricted, conversationSourceId=restriction)
                if text.strip():
                    yield _record('conversation:' + cid + ':node:' + str(node_id) + ':message:' + mid, 'conversation', conversation.get('title'), text, message.get('create_time'), not restricted and role == 'user', meta, [restriction] if restricted else [])
                else:
                    _count(coverage, 'messages_without_text')
    available = {row['path']: row for row in entries}
    if 'group_chats.json' in available:
        groups = _read_json(root / 'group_chats.json')
        for group in groups.get('chats', []):
            _count(coverage, 'group_chats')
            gid = str(group.get('id', ''))
            _need(gid, 'Group chat has no identity.')
            for index, message in enumerate(group.get('messages', [])):
                _check(cancel); _count(coverage, 'group_messages')
                mid = str(message.get('id') or index)
                for file_id in set(_references(message.get('attachments', []))):
                    associations[file_id].add('group:' + gid)
                    restrictions[file_id].add(_hash('group:' + gid))
                if str(message.get('text') or '').strip():
                    yield _record('group:' + gid + ':message:' + mid, 'group', group.get('name'), message['text'], message.get('created_at'), False,
                                  dict(path='group_chats.json', conversationId=gid, messageId=mid, role=message.get('role', 'unspecified'), speaker='unspecified'))
    asset_names = _read_json(root / 'conversation_asset_file_names.json') if 'conversation_asset_file_names.json' in available else {}
    library = _read_json(root / 'library_files.json') if 'library_files.json' in available else []
    library_by_id = defaultdict(list)
    for item in library:
        if isinstance(item, dict) and item.get('file_id'):
            library_by_id[str(item['file_id'])].append(item)
    for rel, entry in available.items():
        _check(cancel)
        path = root.joinpath(*safe_relative(rel).parts)
        if path.suffix.lower() != '.dat':
            continue
        _count(coverage, 'attachment_payloads')
        fid = path.stem
        matching = library_by_id.get(fid, [])
        mapped = asset_names.get(rel)
        metadata_names = {r.get('file_name') for r in matching if isinstance(r.get('file_name'), str)}
        name = mapped if isinstance(mapped, str) else next(iter(metadata_names)) if len(metadata_names) == 1 else rel
        if len(metadata_names) > 1:
            _issues(coverage, 'ambiguous_attachment_name')
        if not mapped and not matching:
            _issues(coverage, 'unmapped_attachment')
        if entry['size_bytes'] > MAX_MEMBER:
            _issues(coverage, 'attachment_too_large'); continue
        data = _files.read_bytes(path, MAX_MEMBER)
        meta = dict(path=rel, assetId=fid, originalName=name, conversationIds=sorted(associations[fid]), relatedConversations=sorted(associations[fid]), linked=bool(associations[fid]))
        count = 0
        for part, text, location in _document(data, name, coverage, cancel=cancel):
            count += 1
            yield _record('attachment:' + fid + ':' + part, 'document', name, text, matching[0].get('created_at') if matching else None,
                          not restrictions[fid], dict(meta, **location), restrictions[fid])
        _count(coverage, 'attachment_segments', count)
        _count(coverage, 'attachments_with_text', bool(count))
    for rel in available:
        _check(cancel)
        path = root.joinpath(*safe_relative(rel).parts)
        if rel.startswith('pages/') and path.suffix == '.json':
            value = _read_json(path)
            if not isinstance(value, dict) or 'projection' not in value:
                _count(coverage, 'page_archival_files'); continue
            _count(coverage, 'page_projections')
            projection, head = value.get('projection') or {}, value.get('head') or {}
            if value.get('includes_current_sequence') is not True or projection.get('projection_through_sequence') != head.get('current_sequence'):
                _issues(coverage, 'stale_page_projection'); continue
            text = projection.get('materialized_search_text') or ''
            if text.strip():
                _count(coverage, 'pages_with_text')
                pid = str(projection.get('page_id') or head.get('page_id') or '')
                _need(pid, 'Page projection has no identity.')
                yield _record('page:' + pid, 'page', projection.get('title'), text, projection.get('updated_at'), False,
                              dict(path=rel, pageId=pid, sequence=head.get('current_sequence'), revision=head.get('revision'), checkpointId=head.get('checkpoint_id')))
        elif rel.startswith('sites/'):
            if path.suffix == '.bundle':
                for part, text, location in _git_bundle(path, coverage, cancel):
                    _count(coverage, 'site_source_segments')
                    yield _record('site:' + rel + ':' + part, 'site', location.get('gitPath', 'Site source'), text, metadata=dict(location, path=rel))
            elif path.suffix == '.sql':
                for part, text, location in _document(_files.read_bytes(path, MAX_MEMBER), rel, coverage, cancel=cancel):
                    _count(coverage, 'site_sql_files')
                    yield _record('site:' + rel + ':' + part, 'site', 'Site database export', text, metadata=dict(location, path=rel, passiveSql=True))
            elif path.suffix == '.json':
                value = _read_json(path)
                if isinstance(value, dict) and 'omissions' in value:
                    for key in ('omissions', 'redactions', 'inventory_warnings', 'incomplete_corpora'):
                        _count(coverage, 'site_' + key, len(value.get(key) or []))
                elif isinstance(value, dict) and isinstance(value.get('metadata'), dict):
                    meta = value['metadata']
                    text = '\n'.join(k + ': ' + str(v) for k, v in meta.items() if isinstance(v, (str, int, float, bool)))
                    if text:
                        _count(coverage, 'site_metadata_records')
                        yield _record('site:' + rel, 'site', meta.get('title') or 'Site metadata', text, metadata=dict(path=rel, corpus=value.get('corpus')))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Private export intake. Normal CLI output contains aggregate counts only.')
    parser.add_argument('--extract-pdf', nargs=2, metavar=('SOURCE', 'OUTPUT'), help=argparse.SUPPRESS)
    parser.add_argument('--coverage', metavar='VERIFIED_STAGE')
    parser.add_argument('--stage', nargs=2, metavar=('SOURCE', 'DESTINATION'))
    parser.add_argument('--expected-manifest')
    args = parser.parse_args()
    try:
        if args.extract_pdf:
            raise SystemExit(_pdf_worker(*args.extract_pdf))
        if args.stage:
            receipt = stage_export(*args.stage, expected_manifest_sha256=args.expected_manifest)
            print(json.dumps({key: receipt[key] for key in ('format', 'manifestSha256', 'files', 'bytes', 'complete', 'stagedRoot')}))
        elif args.coverage:
            coverage = {}
            records, text_bytes = 0, 0
            for record in iter_records(args.coverage, coverage):
                records += 1; text_bytes += len(record['text'].encode('utf-8'))
            print(json.dumps(dict(coverage, emitted_records=records, emitted_text_bytes=text_bytes), sort_keys=True))
        else:
            parser.error('Choose --stage or --coverage.')
    except Exception as exc:
        # Neither extracted text nor private file names belong in terminal errors.
        print(json.dumps(dict(error=type(exc).__name__, message='Private import failed; inspect locally using the import report.')), file=sys.stderr)
        raise SystemExit(1)
