from pathlib import Path
import sys
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import connect, init_db
from app.security import new_salt, hash_password
from datetime import datetime


def create_admin(email: str, password: str, full_name: str = "Админ QA") -> None:
    init_db()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email = ?", (email.lower(),))
    row = cur.fetchone()
    salt = new_salt()
    password_hash = hash_password(password, salt)
    now = datetime.utcnow().isoformat()
    if row:
        cur.execute(
            "UPDATE users SET role = ?, full_name = ?, password_hash = ?, salt = ?, last_login = ? WHERE id = ?",
            ("admin", full_name, password_hash, salt, now, row[0]),
        )
        print(f"Updated existing user {email} to admin.")
    else:
        cur.execute(
            "INSERT INTO users (role, full_name, email, password_hash, salt, last_login) VALUES (?, ?, ?, ?, ?, ?)",
            ("admin", full_name, email.lower(), password_hash, salt, now),
        )
        print(f"Created admin user {email}.")
    conn.commit()
    conn.close()


if __name__ == '__main__':
    create_admin('admin@qa.qa', '123123', 'Админ QA')
