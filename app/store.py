import secrets
import sqlite3
import string
from pathlib import Path

from app.models import Bill

_ALPHABET = string.ascii_uppercase + string.digits


class BillStore:
    def __init__(self, db_path: str | Path = "bills.db"):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS bills (code TEXT PRIMARY KEY, data TEXT)"
        )
        self.db.commit()

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            if not self.db.execute(
                "SELECT 1 FROM bills WHERE code=?", (code,)
            ).fetchone():
                return code

    def save(self, bill: Bill) -> str:
        if not bill.code:
            bill.code = self._new_code()
        self.db.execute(
            "INSERT OR REPLACE INTO bills VALUES (?, ?)",
            (bill.code, bill.model_dump_json()),
        )
        self.db.commit()
        return bill.code

    def load(self, code: str) -> Bill | None:
        row = self.db.execute(
            "SELECT data FROM bills WHERE code=?", (code,)
        ).fetchone()
        return Bill.model_validate_json(row[0]) if row else None
