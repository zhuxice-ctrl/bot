"""数据库模型定义"""
import aiosqlite
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "orders.db"


class Database:
    def __init__(self):
        self.db: Optional[aiosqlite.Connection] = None

    async def init(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(str(DB_PATH))
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()

    async def close(self):
        if self.db:
            await self.db.close()

    async def _create_tables(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT UNIQUE NOT NULL,
                user_qq TEXT NOT NULL,
                product_id TEXT NOT NULL,
                product_name TEXT DEFAULT '',
                amount REAL NOT NULL,
                status TEXT DEFAULT 'paid',
                card_key TEXT DEFAULT '',
                pay_method TEXT DEFAULT '',
                pay_trade_no TEXT DEFAULT '',
                upstream_trade_no TEXT DEFAULT '',
                upstream_pay_url TEXT DEFAULT '',
                created_at REAL NOT NULL,
                delivered_at REAL DEFAULT 0,
                error_msg TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                target_url TEXT NOT NULL,
                description TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS users (
                qq TEXT PRIMARY KEY,
                total_orders INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS card_stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                card_key TEXT NOT NULL,
                status TEXT DEFAULT 'available',
                order_no TEXT DEFAULT '',
                created_at REAL NOT NULL,
                sold_at REAL DEFAULT 0,
                UNIQUE(product_id, card_key)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_qq);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_card_stock_product_status ON card_stock(product_id, status);
        """)
        await self._ensure_columns(
            "orders",
            {
                "upstream_trade_no": "TEXT DEFAULT ''",
                "upstream_pay_url": "TEXT DEFAULT ''",
            },
        )
        await self.db.commit()

    async def _ensure_columns(self, table: str, columns: dict[str, str]):
        cursor = await self.db.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    # === 订单操作 ===
    async def create_order(self, order_no: str, user_qq: str, product_id: str,
                           amount: float, pay_method: str, pay_trade_no: str) -> int:
        cursor = await self.db.execute(
            """INSERT INTO orders (order_no, user_qq, product_id, amount, 
               pay_method, pay_trade_no, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'paid')""",
            (order_no, user_qq, product_id, amount, pay_method, pay_trade_no, time.time())
        )
        await self.db.commit()
        return cursor.lastrowid

    async def update_order_status(self, order_no: str, status: str,
                                  card_key: str = "", error_msg: str = ""):
        delivered_at = time.time() if status == "delivered" else 0
        await self.db.execute(
            """UPDATE orders SET status=?, card_key=?, delivered_at=?, error_msg=?
               WHERE order_no=?""",
            (status, card_key, delivered_at, error_msg, order_no)
        )
        await self.db.commit()

    async def update_upstream_payment(self, order_no: str, upstream_trade_no: str,
                                      upstream_pay_url: str, status: str):
        await self.db.execute(
            """UPDATE orders SET status=?, upstream_trade_no=?, upstream_pay_url=?, error_msg=''
               WHERE order_no=?""",
            (status, upstream_trade_no, upstream_pay_url, order_no)
        )
        await self.db.commit()

    async def get_order(self, order_no: str) -> Optional[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM orders WHERE order_no=?", (order_no,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_orders(self, user_qq: str, limit: int = 10) -> list:
        cursor = await self.db.execute(
            "SELECT * FROM orders WHERE user_qq=? ORDER BY created_at DESC LIMIT ?",
            (user_qq, limit)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # === 商品操作 ===
    async def get_product(self, product_id: str) -> Optional[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM products WHERE product_id=? AND enabled=1", (product_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_products(self) -> list:
        cursor = await self.db.execute(
            "SELECT * FROM products WHERE enabled=1"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def add_product(self, product_id: str, name: str, price: float,
                          target_url: str, description: str = ""):
        await self.db.execute(
            """INSERT OR REPLACE INTO products (product_id, name, price, target_url, description, enabled)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (product_id, name, price, target_url, description)
        )
        await self.db.commit()

    # === 本地卡密库存 ===
    async def add_card_stock(self, product_id: str, card_keys: list[str]) -> int:
        now = time.time()
        inserted = 0
        for card_key in card_keys:
            clean_key = card_key.strip()
            if not clean_key:
                continue
            cursor = await self.db.execute(
                """INSERT OR IGNORE INTO card_stock (product_id, card_key, created_at)
                   VALUES (?, ?, ?)""",
                (product_id, clean_key, now)
            )
            inserted += max(cursor.rowcount, 0)
        await self.db.commit()
        return inserted

    async def reserve_card_stock(self, product_id: str, order_no: str) -> Optional[str]:
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await self.db.execute(
                """SELECT id, card_key FROM card_stock
                   WHERE product_id=? AND status='available'
                   ORDER BY id ASC LIMIT 1""",
                (product_id,)
            )
            row = await cursor.fetchone()
            if not row:
                await self.db.rollback()
                return None

            await self.db.execute(
                """UPDATE card_stock SET status='sold', order_no=?, sold_at=?
                   WHERE id=?""",
                (order_no, time.time(), row["id"])
            )
            await self.db.commit()
            return row["card_key"]
        except Exception:
            await self.db.rollback()
            raise

    async def get_stock_count(self, product_id: str) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM card_stock WHERE product_id=? AND status='available'",
            (product_id,)
        )
        row = await cursor.fetchone()
        return int(row[0])

    async def get_all_stock_counts(self) -> list[dict]:
        cursor = await self.db.execute(
            """SELECT p.product_id, p.name, COUNT(cs.id) AS stock
               FROM products p
               LEFT JOIN card_stock cs
                    ON cs.product_id = p.product_id AND cs.status='available'
               WHERE p.enabled=1
               GROUP BY p.product_id, p.name
               ORDER BY p.id ASC"""
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # === 系统设置 ===
    async def set_setting(self, key: str, value: str):
        await self.db.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, time.time())
        )
        await self.db.commit()

    async def get_setting(self, key: str) -> Optional[str]:
        cursor = await self.db.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (key,)
        )
        row = await cursor.fetchone()
        return str(row["value"]) if row else None

    # === 用户操作 ===
    async def ensure_user(self, qq: str):
        await self.db.execute(
            "INSERT OR IGNORE INTO users (qq, created_at) VALUES (?, ?)",
            (qq, time.time())
        )
        await self.db.commit()

    async def increment_user_stats(self, qq: str, amount: float):
        await self.ensure_user(qq)
        await self.db.execute(
            "UPDATE users SET total_orders = total_orders + 1, total_spent = total_spent + ? WHERE qq=?",
            (amount, qq)
        )
        await self.db.commit()
