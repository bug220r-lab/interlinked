#!/usr/bin/env python3
"""Verify or restore a codex-project-export-v1 package. Python 3.12, stdlib only.

No archived code is executed. Restore requires a fresh destination, verifies
the entire package first, and streams individual files without an extraction
cache. A failed restore is left in place for inspection, never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile

CHUNK = 1024 * 1024
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)


class PackageError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as source:
        return json.load(source, object_pairs_hook=no_duplicate_keys)


def integer(value, label: str, nonnegative: bool = True) -> int:
    require(type(value) is int, f"{label} must be an integer")
    require(not nonnegative or value >= 0, f"{label} must not be negative")
    return value


def digest(value, label: str) -> str:
    require(isinstance(value, str) and SHA_RE.fullmatch(value) is not None,
            f"{label} must be a lowercase SHA-256 digest")
    return value


def safe_relative(value, label: str = "path") -> str:
    require(isinstance(value, str) and bool(value), f"Empty or invalid {label}")
    normalized = value.replace("\\", "/")
    require(not normalized.startswith("/"), f"Absolute {label} is forbidden: {value}")
    for component in normalized.split("/"):
        require(component not in ("", ".", ".."), f"Invalid {label} component: {value}")
        require(not any(ord(c) < 32 or c in '<>:"|?*' for c in component),
                f"Invalid Windows filename in {label}: {value}")
        require(not component.endswith((" ", ".")), f"Ambiguous Windows {label}: {value}")
        require(RESERVED.fullmatch(component) is None, f"Reserved Windows {label}: {value}")
    return normalized


def package_name(value) -> str:
    name = safe_relative(value, "package filename")
    require("/" not in name, "Package files must be direct children of the package folder")
    return name


def no_links(path: Path) -> None:
    """Reject symlinks/junctions in existing path components, without resolving them."""
    for component in (path, *path.parents):
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
            raise PackageError(f"Symlink or junction not permitted: {component}")


def regular_file(path: Path) -> None:
    no_links(path)
    require(path.is_file() and stat.S_ISREG(path.stat().st_mode), f"Missing regular file: {path}")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_rows(path: Path):
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, 1):
            require(bool(line.strip()), f"Blank manifest row {line_number}")
            try:
                row = json.loads(line, object_pairs_hook=no_duplicate_keys)
            except (json.JSONDecodeError, PackageError) as exc:
                raise PackageError(f"Invalid manifest row {line_number}: {exc}") from exc
            require(isinstance(row, dict), f"Manifest row {line_number} is not an object")
            yield row


def stream_blob(zf: zipfile.ZipFile, member: str, expected_sha: str,
                expected_bytes: int, output=None) -> None:
    h = hashlib.sha256()
    total = 0
    with zf.open(member, "r") as source:
        while True:
            data = source.read(min(CHUNK, max(1, expected_bytes - total + 1)))
            if not data:
                break
            total += len(data)
            require(total <= expected_bytes, f"Blob exceeds expected size: {member}")
            h.update(data)
            if output is not None:
                output.write(data)
    require(total == expected_bytes, f"Blob length mismatch: {member}")
    require(h.hexdigest() == expected_sha, f"Blob SHA-256 mismatch: {member}")


def verify_package(location: str | Path, quiet: bool = False):
    requested = Path(location).absolute()
    root = requested.parent if requested.name.lower() == "catalog.json" else requested
    no_links(root)
    require(root.is_dir(), f"Package folder does not exist: {root}")
    catalog_path = root / "catalog.json"
    regular_file(catalog_path)
    catalog_hash = file_hash(catalog_path)
    cat = read_json(catalog_path)
    require(isinstance(cat, dict), "Catalog must be an object")
    require(cat.get("format") == "codex-project-export-v1", "Unknown export format")
    require(cat.get("status") in ("VERIFIED", "VERIFIED_WITH_EXCLUSIONS"),
            "Catalog does not mark this export verified")
    expected_count = integer(cat.get("files"), "files")
    expected_total = integer(cat.get("logicalBytes"), "logicalBytes")
    require(expected_count > 0, "Package has no files")
    require(isinstance(cat.get("parts"), list) and cat["parts"], "No ZIP parts listed")
    require(isinstance(cat.get("blobs"), dict) and cat["blobs"], "No blobs listed")
    names = {"catalog.json"}
    support = {}
    for label in ("manifest", "exclusions"):
        descriptor = cat.get(label)
        require(isinstance(descriptor, dict), f"Missing {label} descriptor")
        name = package_name(descriptor.get("name"))
        require(name.casefold() not in names, f"Duplicate package filename: {name}")
        names.add(name.casefold())
        expected_sha = digest(descriptor.get("sha256"), f"{label} sha256")
        path = root / name
        regular_file(path)
        require(file_hash(path) == expected_sha, f"{label} SHA-256 mismatch")
        support[label] = path
    parts = {}
    for part in cat["parts"]:
        require(isinstance(part, dict), "Invalid part descriptor")
        name = package_name(part.get("name"))
        require(name.casefold() not in names, f"Duplicate package filename: {name}")
        names.add(name.casefold())
        expected_sha = digest(part.get("sha256"), "part sha256")
        expected_bytes = integer(part.get("bytes"), "part bytes")
        path = root / name
        regular_file(path)
        require(path.stat().st_size == expected_bytes, f"Part length mismatch: {name}")
        require(file_hash(path) == expected_sha, f"Part SHA-256 mismatch: {name}")
        parts[name] = {"path": path, "members": {}}
    blobs = cat["blobs"]
    for sha, descriptor in blobs.items():
        digest(sha, "blob key")
        require(isinstance(descriptor, dict), f"Invalid blob descriptor: {sha}")
        part = descriptor.get("part")
        require(isinstance(part, str) and part in parts, f"Unknown part for blob: {sha}")
        member = descriptor.get("member")
        require(member == f"blobs/{sha}", f"Invalid blob member name: {member}")
        integer(descriptor.get("bytes"), "blob bytes")
        require(member not in parts[part]["members"], f"Duplicate blob member: {member}")
        parts[part]["members"][member] = sha
    referenced = set()
    files_seen, dirs_seen = set(), set()
    row_count, logical_bytes = 0, 0
    for row in manifest_rows(support["manifest"]):
        relative = safe_relative(row.get("path"))
        key = relative.casefold()
        require(key not in files_seen and key not in dirs_seen,
                f"Duplicate or conflicting destination: {relative}")
        segments = key.split("/")
        for i in range(1, len(segments)):
            parent = "/".join(segments[:i])
            require(parent not in files_seen, f"File/directory conflict: {relative}")
            dirs_seen.add(parent)
        files_seen.add(key)
        sha = digest(row.get("sha256"), "manifest sha256")
        require(sha in blobs, f"Manifest references unknown blob: {relative}")
        count = integer(row.get("bytes"), "manifest bytes")
        require(count == blobs[sha]["bytes"], f"Conflicting blob size: {relative}")
        integer(row.get("mtime_ns"), "mtime_ns", nonnegative=False)
        require(isinstance(row.get("source"), str), "Manifest source must be a string")
        require(isinstance(row.get("capture_method"), str), "Manifest capture_method must be a string")
        referenced.add(sha)
        row_count += 1
        logical_bytes += count
    require(row_count == expected_count, "Manifest file count does not match catalog")
    require(logical_bytes == expected_total, "Manifest logical byte total does not match catalog")
    require(referenced == set(blobs), "Catalog contains unreferenced blobs")
    for name, part in parts.items():
        with zipfile.ZipFile(part["path"], "r") as zf:
            infos = zf.infolist()
            entries = [info.filename for info in infos]
            require(len(entries) == len(set(entries)), f"Duplicate ZIP members in {name}")
            require(set(entries) == set(part["members"]), f"Unlisted or missing ZIP members in {name}")
            for info in infos:
                mode = (info.external_attr >> 16) & 0xFFFF
                require(not info.is_dir() and not stat.S_ISLNK(mode), f"Nonregular ZIP member: {info.filename}")
                require(not mode or stat.S_IFMT(mode) in (0, stat.S_IFREG), f"Nonregular ZIP mode: {info.filename}")
                require(not info.flag_bits & 1, "Encrypted ZIP members are unsupported")
                sha = part["members"][info.filename]
                require(info.file_size == blobs[sha]["bytes"], f"ZIP size differs from catalog: {info.filename}")
                stream_blob(zf, info.filename, sha, blobs[sha]["bytes"])
        if not quiet:
            print(f"Verified ZIP part: {name}", flush=True)
    require(file_hash(catalog_path) == catalog_hash, "Catalog changed during verification")
    require(file_hash(support["manifest"]) == cat["manifest"]["sha256"], "Manifest changed during verification")
    if not quiet:
        print(f"VERIFIED: {row_count:,} files, {logical_bytes:,} logical bytes, {len(blobs):,} unique blobs.")
        if cat["status"] == "VERIFIED_WITH_EXCLUSIONS":
            print(f"Export includes recorded exclusions: {support['exclusions'].name}")
    return {"root": root, "catalog": cat, "catalog_hash": catalog_hash,
            "manifest": support["manifest"], "parts": parts}


def restore_package(location: str | Path, destination: str | Path, quiet: bool = False):
    dest = Path(destination).absolute()
    require(not dest.exists() and not dest.is_symlink(), f"Destination already exists: {dest}")
    no_links(dest)
    verified = verify_package(location, quiet=quiet)
    root, cat = verified["root"], verified["catalog"]
    require(not dest.is_relative_to(root), "Restore destination must not be inside the package")
    existing_parent = dest.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    available = shutil.disk_usage(existing_parent).free
    require(available >= cat["logicalBytes"] + 64 * 1024 * 1024, "Insufficient free space for restore plus 64 MiB reserve")
    no_links(dest)
    dest.mkdir(parents=True, exist_ok=False)
    restored, bytes_written, mtime_warnings = 0, 0, []
    current_part, current_zip = None, None
    try:
        for row in manifest_rows(verified["manifest"]):
            relative = safe_relative(row["path"])
            target = dest.joinpath(*relative.split("/"))
            require(target.is_relative_to(dest), "Restore path escaped destination")
            no_links(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = cat["blobs"][row["sha256"]]
            if current_part != descriptor["part"]:
                if current_zip is not None:
                    current_zip.close()
                current_part = descriptor["part"]
                regular_file(verified["parts"][current_part]["path"])
                current_zip = zipfile.ZipFile(verified["parts"][current_part]["path"], "r")
            with target.open("xb") as output:
                stream_blob(current_zip, descriptor["member"], row["sha256"], row["bytes"], output)
            require(target.stat().st_size == row["bytes"] and file_hash(target) == row["sha256"],
                    f"Restored file readback mismatch: {relative}")
            try:
                os.utime(target, ns=(row["mtime_ns"], row["mtime_ns"]))
            except (OSError, OverflowError, ValueError) as exc:
                mtime_warnings.append({"path": relative, "error": str(exc)})
            restored += 1
            bytes_written += row["bytes"]
            if not quiet and restored % 1000 == 0:
                print(f"Restored and verified {restored:,}/{cat['files']:,} files", flush=True)
    finally:
        if current_zip is not None:
            current_zip.close()
    require(restored == cat["files"] and bytes_written == cat["logicalBytes"], "Restore totals differ from catalog")
    require(file_hash(root / "catalog.json") == verified["catalog_hash"], "Catalog changed during restore")
    require(file_hash(verified["manifest"]) == cat["manifest"]["sha256"], "Manifest changed during restore")
    report = {"status": "RESTORED_AND_VERIFIED", "files": restored,
              "bytes": bytes_written, "destination": str(dest), "mtimeWarnings": mtime_warnings}
    if not quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify", metavar="PACKAGE_FOLDER")
    mode.add_argument("--restore", metavar="PACKAGE_FOLDER")
    parser.add_argument("--destination", metavar="NEW_FOLDER")
    args = parser.parse_args(argv)
    if args.restore and not args.destination:
        parser.error("--restore requires --destination")
    if args.verify and args.destination:
        parser.error("--destination is only valid with --restore")
    try:
        if args.verify:
            verify_package(args.verify)
        else:
            restore_package(args.restore, args.destination)
    except (PackageError, OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        if args.restore:
            print("Any partial destination is retained. Resolve the error and choose a NEW destination.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
