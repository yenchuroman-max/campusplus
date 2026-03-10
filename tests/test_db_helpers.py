from app.db import PostgresCursorAdapter, insert_ignore


class _FakeCursor:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = 1

    def execute(self, sql: str, params: tuple):
        self.calls.append((sql, params))
        return self


def test_insert_ignore_sqlite(monkeypatch):
    monkeypatch.setattr("app.db._use_postgres", lambda: False)
    cur = _FakeCursor()

    changed = insert_ignore(
        cur,
        "teacher_disciplines",
        ("teacher_id", "discipline_id"),
        (1, 2),
        conflict_columns=("teacher_id", "discipline_id"),
    )

    assert changed == 1
    assert cur.calls == [
        (
            "INSERT OR IGNORE INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?)",
            (1, 2),
        )
    ]


class _FakePostgresRawCursor:
    def __init__(self, rowcount: int, lastval: int | None = None):
        self.rowcount = rowcount
        self.lastval = lastval
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):
        self.calls.append((sql, params))
        if sql == "SELECT LASTVAL() AS last_id":
            self.rowcount = 1
        return self

    def executemany(self, sql: str, seq_of_params):
        self.calls.append((sql, tuple(seq_of_params)))
        return self

    def fetchone(self):
        if self.lastval is None:
            return None
        return {"last_id": self.lastval}

    def fetchall(self):
        return []


def test_postgres_cursor_adapter_preserves_insert_rowcount_on_success():
    raw = _FakePostgresRawCursor(rowcount=1, lastval=42)
    cur = PostgresCursorAdapter(raw)

    cur.execute("INSERT INTO disciplines (name) VALUES (?)", ("GIS",))

    assert cur.rowcount == 1
    assert cur.lastrowid == 42
    assert raw.calls == [
        ("INSERT INTO disciplines (name) VALUES (%s)", ("GIS",)),
        ("SELECT LASTVAL() AS last_id", ()),
    ]


def test_postgres_cursor_adapter_skips_lastval_when_insert_ignored():
    raw = _FakePostgresRawCursor(rowcount=0, lastval=42)
    cur = PostgresCursorAdapter(raw)

    cur.execute(
        "INSERT INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?) ON CONFLICT (teacher_id, discipline_id) DO NOTHING",
        (1, 2),
    )

    assert cur.rowcount == 0
    assert cur.lastrowid is None
    assert raw.calls == [
        (
            "INSERT INTO teacher_disciplines (teacher_id, discipline_id) VALUES (%s, %s) ON CONFLICT (teacher_id, discipline_id) DO NOTHING",
            (1, 2),
        )
    ]


def test_insert_ignore_postgres(monkeypatch):
    monkeypatch.setattr("app.db._use_postgres", lambda: True)
    cur = _FakeCursor()

    changed = insert_ignore(
        cur,
        "teacher_disciplines",
        ("teacher_id", "discipline_id"),
        (1, 2),
        conflict_columns=("teacher_id", "discipline_id"),
    )

    assert changed == 1
    assert cur.calls == [
        (
            "INSERT INTO teacher_disciplines (teacher_id, discipline_id) VALUES (?, ?) ON CONFLICT (teacher_id, discipline_id) DO NOTHING",
            (1, 2),
        )
    ]
