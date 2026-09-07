"""Copy-and-verify data migration; the original directory is never removed."""
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.location-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_data_dir(default, pointer):
    if not pointer.is_file():
        return default
    target = Path(json.loads(pointer.read_text(encoding='utf-8'))['data_dir'])
    if not target.is_absolute() or not target.is_dir():
        raise ValueError('Configured data directory is unavailable; reconnect it before starting')
    return target


def migrate_data(source, destination, pointer):
    source, destination, pointer = Path(source).resolve(), Path(destination).absolute(), Path(pointer)
    for parent in [destination, *destination.parents]:
        if parent.exists() and getattr(parent.stat(follow_symlinks=False), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('Data destination cannot use a symbolic link or junction')
    destination = destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Data directories must not contain each other')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Choose a new or empty data directory')
    files = []
    for directory, names, filenames in os.walk(source, followlinks=False):
        for name in names + filenames:
            path = Path(directory) / name
            if path.is_symlink() or getattr(path.stat(follow_symlinks=False), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Data source contains an unsupported link; original preserved')
        files.extend(Path(directory) / name for name in filenames)
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        with path.open('rb') as left, target.open('rb') as right:
            if hashlib.file_digest(left, 'sha256').digest() != hashlib.file_digest(right, 'sha256').digest():
                raise ValueError('Copied data failed verification; original preserved')

    def rebase(value):
        path = Path(value)
        return str(destination / path.relative_to(source)) if path.is_absolute() and path.is_relative_to(source) else value

    manifest = destination / 'knowledge/papers/manifest.json'
    if manifest.is_file():
        records = json.loads(manifest.read_text(encoding='utf-8'))
        for record in records:
            record['stored_path'] = rebase(record['stored_path'])
        atomic_json(manifest, records)
    database = destination / 'knowledge/index/metadata.sqlite3'
    if database.is_file():
        db = sqlite3.connect(database)
        try:
            with db:
                for row in db.execute('SELECT chunk_id, source_path FROM chunks').fetchall():
                    db.execute('UPDATE chunks SET source_path=? WHERE chunk_id=?', (rebase(row[1]), row[0]))
        finally:
            db.close()
    history = destination / 'history.sqlite3'
    if history.is_file():
        db = sqlite3.connect(history)
        try:
            with db:
                for key, payload in db.execute('SELECT draft_id,payload FROM card_drafts').fetchall():
                    item = json.loads(payload)
                    if item.get('original') and item['original'].get('path'):
                        item['original']['path'] = rebase(item['original']['path'])
                    db.execute('UPDATE card_drafts SET payload=? WHERE draft_id=?', (json.dumps(item, ensure_ascii=False), key))
        finally:
            db.close()
    atomic_json(pointer, {'data_dir': str(destination), 'previous_data_dir': str(source)})
    return {'restart_required': True, 'data_dir': str(destination), 'original_preserved': True, 'verified_files': len(files)}
