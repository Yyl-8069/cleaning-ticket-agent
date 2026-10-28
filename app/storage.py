"""SQLite persistence. Every upload and analysis has its own immutable ID."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    """生成供数据库和日志使用的 UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path):
        """建立数据库表，并将重启前未完成的运行标为中断。"""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                    status TEXT NOT NULL, result TEXT, error_type TEXT, created_at TEXT NOT NULL,
                    finished_at TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    at TEXT NOT NULL, event_type TEXT NOT NULL, summary TEXT NOT NULL);
            ''')
            db.execute("UPDATE runs SET status=?, finished_at=? WHERE status IN ('queued','running')",
                       ('interrupted', now()))

    def _db(self):
        """创建启用外键约束的 SQLite 连接。"""
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def save_case(self, bundle):
        """保存一次上传的案例快照，返回独立案例编号。"""
        case_id = str(uuid4())
        with self._db() as db:
            db.execute('INSERT INTO cases VALUES (?,?,?,?)',
                       (case_id, bundle['ticket']['ticket_id'], json.dumps(bundle, ensure_ascii=False), now()))
        return case_id

    def get_case(self, case_id):
        """按案例编号取回完整模拟数据。"""
        with self._db() as db:
            row = db.execute('SELECT payload FROM cases WHERE id=?', (case_id,)).fetchone()
        if row is None:
            raise KeyError('case not found')
        return json.loads(row['payload'])

    def create_run(self, case_id):
        """为已保存案例创建一条待处理分析运行。"""
        run_id = str(uuid4())
        with self._db() as db:
            db.execute('INSERT INTO runs (id,case_id,status,created_at) VALUES (?,?,?,?)',
                       (run_id, case_id, 'queued', now()))
        return run_id

    def get_run(self, run_id):
        """读取运行状态、分析结果和执行事件。"""
        with self._db() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            events = db.execute('SELECT at,event_type,summary FROM events WHERE run_id=? ORDER BY id', (run_id,)).fetchall()
        if row is None:
            raise KeyError('run not found')
        result = dict(row)
        result['result'] = json.loads(result['result']) if result['result'] else None
        result['events'] = [dict(e) for e in events]
        return result

    def set_run(self, run_id, status, result=None, error_type=None):
        """更新分析状态，并在终态写入结果或错误类型。"""
        with self._db() as db:
            db.execute('UPDATE runs SET status=?, result=?, error_type=?, finished_at=? WHERE id=?',
                       (status, json.dumps(result, ensure_ascii=False) if result else None,
                        error_type, now() if status not in ('queued', 'running') else None, run_id))

    def event(self, run_id, event_type, summary):
        """保存可供页面追溯的精简运行事件。"""
        with self._db() as db:
            db.execute('INSERT INTO events(run_id,at,event_type,summary) VALUES(?,?,?,?)',
                       (run_id, now(), event_type, summary))
