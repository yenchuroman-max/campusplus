from app.db import insert_ignore


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
