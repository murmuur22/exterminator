"""SQLite storage; each operation owns and closes its connection."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
            columns = [tuple(row)[1:] for row in db.execute('PRAGMA table_info(reports)')]
            expected = [('id', 'INTEGER', 0, None, 1)] + [
                (name, 'TEXT', 1, None, 0) for name in
                ('title', 'service', 'kind', 'detail', 'status', 'created_at', 'updated_at')]
            if version not in (0, 1) or (tables and (tables != {'reports'} or columns != expected)) or (version == 1 and not tables):
                raise sqlite3.DatabaseError('Unsupported schema')
            db.execute('''CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, service TEXT NOT NULL, kind TEXT NOT NULL,
                detail TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )''')
            db.execute('PRAGMA user_version=1')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def health(self):
        with self.connection() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise sqlite3.DatabaseError('Unsupported schema')
            db.execute('SELECT id FROM reports LIMIT 1').fetchone()

    def create(self, fields):
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            result = db.execute(
                'INSERT INTO reports (title,service,kind,detail,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                (fields['title'], fields.get('service', ''), fields.get('kind', 'bug'),
                 fields.get('detail', ''), 'open', now, now))
            return dict(db.execute('SELECT * FROM reports WHERE id=?', (result.lastrowid,)).fetchone())

    def update(self, report_id, fields):
        allowed = ('title', 'service', 'kind', 'detail', 'status')
        changes = {key: fields[key] for key in allowed if key in fields}
        changes['updated_at'] = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            assignments = ', '.join(f'{key}=?' for key in changes)
            db.execute(f'UPDATE reports SET {assignments} WHERE id=?', (*changes.values(), report_id))
            row = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
            return dict(row) if row else None

    def list(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM reports ORDER BY id DESC')]
