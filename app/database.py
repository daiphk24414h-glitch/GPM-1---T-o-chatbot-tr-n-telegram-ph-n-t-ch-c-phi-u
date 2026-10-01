import sqlite3
from datetime import date
from pathlib import Path


class UserStore:
    def __init__(self, path: Path, admin_id: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.admin_id = path, admin_id
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, approved_by INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS agent_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, request TEXT NOT NULL, tool TEXT NOT NULL, route TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            db.execute("""CREATE TABLE IF NOT EXISTS positions (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                entry_date TEXT NOT NULL,
                high_watermark REAL NOT NULL,
                active_stop REAL NOT NULL DEFAULT 0,
                last_price REAL,
                last_action TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, symbol)
            )""")
            db.execute("INSERT OR IGNORE INTO users(user_id, approved_by) VALUES (?, ?)", (admin_id, admin_id))

    def connect(self):
        return sqlite3.connect(self.path)

    def approved(self, user_id: int) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is not None

    def approve(self, user_id: int) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO users(user_id, approved_by) VALUES (?, ?)", (user_id, self.admin_id))

    def list_users(self) -> list[int]:
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT user_id FROM users ORDER BY user_id")]

    def record_agent_event(self, user_id: int, request: str, tool: str, route: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO agent_events(user_id, request, tool, route) VALUES (?, ?, ?, ?)",
                       (user_id, request[:500], tool[:50], route[:30]))

    def upsert_position(self, user_id: int, chat_id: int, symbol: str, quantity: int,
                        entry_price: float, entry_date: str | None = None) -> None:
        entry_date = entry_date or date.today().isoformat()
        with self.connect() as db:
            db.execute("""INSERT INTO positions
                (user_id, chat_id, symbol, quantity, entry_price, entry_date, high_watermark, active_stop)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(user_id, symbol) DO UPDATE SET
                    chat_id=excluded.chat_id, quantity=excluded.quantity,
                    entry_price=excluded.entry_price, entry_date=excluded.entry_date,
                    high_watermark=excluded.high_watermark, active_stop=0,
                    last_price=NULL, last_action=NULL, updated_at=CURRENT_TIMESTAMP""",
                (user_id, chat_id, symbol.upper(), quantity, entry_price, entry_date, entry_price))

    def get_position(self, user_id: int, symbol: str) -> dict | None:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM positions WHERE user_id=? AND symbol=?",
                             (user_id, symbol.upper())).fetchone()
            return dict(row) if row else None

    def list_positions(self, user_id: int) -> list[dict]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM positions WHERE user_id=? ORDER BY symbol", (user_id,))]

    def list_all_positions(self) -> list[dict]:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                "SELECT * FROM positions ORDER BY user_id, symbol")]

    def update_position_risk(self, user_id: int, symbol: str, high_watermark: float,
                             active_stop: float, last_price: float, last_action: str) -> None:
        with self.connect() as db:
            db.execute("""UPDATE positions SET high_watermark=?, active_stop=?, last_price=?,
                       last_action=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND symbol=?""",
                       (high_watermark, active_stop, last_price, last_action, user_id, symbol.upper()))

    def remove_position(self, user_id: int, symbol: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM positions WHERE user_id=? AND symbol=?",
                                (user_id, symbol.upper()))
            return cursor.rowcount > 0
