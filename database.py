import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


# =========================
# DATABASE
# =========================

DB_PATH = Path("bot.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # =========================
    # USERS
    # =========================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            name TEXT,
            username TEXT,
            balance INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # =========================
    # SUBSCRIPTIONS
    # =========================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            package_code TEXT,
            package_name TEXT,
            price INTEGER DEFAULT 0,
            start_date TEXT,
            expiry_date TEXT,
            status TEXT DEFAULT 'inactive',
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        )
    """)

    # =========================
    # TRANSACTIONS
    # =========================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            amount INTEGER DEFAULT 0,
            payment_method TEXT,
            proof_file_id TEXT,
            package_code TEXT,
            package_name TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            processed_at TEXT
        )
    """)

    # =========================
    # FORMAT SETTINGS
    # =========================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS format_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            template TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    # =========================
    # FORMAT RESULTS
    # =========================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS format_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            input_text TEXT,
            result_text TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # =========================
    # FORMAT HISTORY
    # =========================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS format_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            input_text TEXT,
            result_text TEXT,
            created_at TEXT NOT NULL,
            deleted_at TEXT NOT NULL
        )
    """)

    # =========================
    # JMO SOLUTIONS
    # =========================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jmo_solutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            keywords TEXT,
            error_code TEXT,
            problem TEXT,
            solution TEXT,
            source TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# =========================
# USER
# =========================

def upsert_user(
    telegram_id: int,
    name: str,
    username: str | None
):
    now = datetime.now().isoformat()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (
            telegram_id,
            name,
            username,
            balance,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, 0, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            name = excluded.name,
            username = excluded.username,
            updated_at = excluded.updated_at
    """, (
        telegram_id,
        name,
        username,
        now,
        now
    ))

    conn.commit()
    conn.close()


def get_user(telegram_id: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM users
        WHERE telegram_id = ?
    """, (telegram_id,))

    user = cursor.fetchone()

    conn.close()

    return user


# =========================
# SALDO
# =========================

def get_balance(telegram_id: int) -> int:
    user = get_user(telegram_id)

    if not user:
        return 0

    return user["balance"]


def add_balance(
    telegram_id: int,
    amount: int
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET balance = balance + ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        amount,
        datetime.now().isoformat(),
        telegram_id
    ))

    conn.commit()
    conn.close()


def subtract_balance(
    telegram_id: int,
    amount: int
) -> bool:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT balance
        FROM users
        WHERE telegram_id = ?
    """, (telegram_id,))

    user = cursor.fetchone()

    if not user:
        conn.close()
        return False

    if user["balance"] < amount:
        conn.close()
        return False

    cursor.execute("""
        UPDATE users
        SET balance = balance - ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        amount,
        datetime.now().isoformat(),
        telegram_id
    ))

    conn.commit()
    conn.close()

    return True


# =========================
# SUBSCRIPTION
# =========================

def save_subscription(
    telegram_id: int,
    package_code: str,
    package_name: str,
    price: int,
    months: int | None
):
    start = datetime.now()

    if months is None:
        expiry = None
        status = "unlimited"
    else:
        expiry = start + timedelta(days=30 * months)
        status = "active"

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO subscriptions (
            telegram_id,
            package_code,
            package_name,
            price,
            start_date,
            expiry_date,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            package_code = excluded.package_code,
            package_name = excluded.package_name,
            price = excluded.price,
            start_date = excluded.start_date,
            expiry_date = excluded.expiry_date,
            status = excluded.status
    """, (
        telegram_id,
        package_code,
        package_name,
        price,
        start.isoformat(),
        expiry.isoformat() if expiry else None,
        status
    ))

    conn.commit()
    conn.close()


def get_subscription(telegram_id: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM subscriptions
        WHERE telegram_id = ?
    """, (telegram_id,))

    subscription = cursor.fetchone()

    conn.close()

    if not subscription:
        return None

    if subscription["status"] == "active":
        expiry = subscription["expiry_date"]

        if expiry:
            expiry_dt = datetime.fromisoformat(expiry)

            if datetime.now() >= expiry_dt:
                conn = get_connection()
                conn.execute("""
                    UPDATE subscriptions
                    SET status = 'expired'
                    WHERE telegram_id = ?
                """, (telegram_id,))
                conn.commit()
                conn.close()

                return get_subscription(telegram_id)

    return subscription


def has_active_auto_format(
    telegram_id: int
) -> bool:
    subscription = get_subscription(telegram_id)

    if not subscription:
        return False

    return subscription["status"] in (
        "active",
        "unlimited"
    )


# =========================
# TRANSACTIONS
# =========================

def create_transaction(
    telegram_id: int,
    amount: int,
    payment_method: str,
    proof_file_id: str,
    package_code: str,
    package_name: str
) -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO transactions (
            telegram_id,
            amount,
            payment_method,
            proof_file_id,
            package_code,
            package_name,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (
        telegram_id,
        amount,
        payment_method,
        proof_file_id,
        package_code,
        package_name,
        datetime.now().isoformat()
    ))

    transaction_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return transaction_id


def get_transaction(transaction_id: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM transactions
        WHERE id = ?
    """, (transaction_id,))

    transaction = cursor.fetchone()

    conn.close()

    return transaction


def get_pending_transactions():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM transactions
        WHERE status = 'pending'
        ORDER BY id DESC
    """)

    transactions = cursor.fetchall()

    conn.close()

    return transactions


def update_transaction_status(
    transaction_id: int,
    status: str
):
    conn = get_connection()

    conn.execute("""
        UPDATE transactions
        SET status = ?,
            processed_at = ?
        WHERE id = ?
    """, (
        status,
        datetime.now().isoformat(),
        transaction_id
    ))

    conn.commit()
    conn.close()


# =========================
# FORMAT SETTING
# =========================

def save_format_template(
    telegram_id: int,
    template: str
):
    conn = get_connection()

    conn.execute("""
        INSERT INTO format_settings (
            telegram_id,
            template,
            updated_at
        )
        VALUES (?, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            template = excluded.template,
            updated_at = excluded.updated_at
    """, (
        telegram_id,
        template,
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def get_format_template(
    telegram_id: int
):
    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT template
        FROM format_settings
        WHERE telegram_id = ?
    """, (telegram_id,))

    result = cursor.fetchone()

    conn.close()

    if not result:
        return None

    return result["template"]


# =========================
# FORMAT RESULTS
# =========================

def save_format_result(
    telegram_id: int,
    input_text: str,
    result_text: str
) -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO format_results (
            telegram_id,
            input_text,
            result_text,
            created_at
        )
        VALUES (?, ?, ?, ?)
    """, (
        telegram_id,
        input_text,
        result_text,
        datetime.now().isoformat()
    ))

    result_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return result_id


def get_format_results(
    telegram_id: int
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM format_results
        WHERE telegram_id = ?
        ORDER BY id DESC
    """, (telegram_id,))

    results = cursor.fetchall()

    conn.close()

    return results


def get_format_result(
    result_id: int,
    telegram_id: int
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM format_results
        WHERE id = ?
        AND telegram_id = ?
    """, (
        result_id,
        telegram_id
    ))

    result = cursor.fetchone()

    conn.close()

    return result


# =========================
# MOVE RESULT TO HISTORY
# =========================

def move_result_to_history(
    result_id: int,
    telegram_id: int
) -> bool:
    result = get_format_result(
        result_id,
        telegram_id
    )

    if not result:
        return False

    conn = get_connection()

    conn.execute("""
        INSERT INTO format_history (
            telegram_id,
            input_text,
            result_text,
            created_at,
            deleted_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        result["telegram_id"],
        result["input_text"],
        result["result_text"],
        result["created_at"],
        datetime.now().isoformat()
    ))

    conn.execute("""
        DELETE FROM format_results
        WHERE id = ?
        AND telegram_id = ?
    """, (
        result_id,
        telegram_id
    ))

    conn.commit()
    conn.close()

    return True


def get_format_history(
    telegram_id: int
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM format_history
        WHERE telegram_id = ?
        ORDER BY id DESC
    """, (telegram_id,))

    history = cursor.fetchall()

    conn.close()

    return history


# =========================
# JMO SOLUTIONS
# =========================

def add_jmo_solution(
    title: str,
    keywords: str,
    error_code: str | None,
    problem: str,
    solution: str,
    source: str | None = None
):
    conn = get_connection()

    conn.execute("""
        INSERT INTO jmo_solutions (
            title,
            keywords,
            error_code,
            problem,
            solution,
            source,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        keywords,
        error_code,
        problem,
        solution,
        source,
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()


def search_jmo_solution(
    query: str
):
    conn = get_connection()
    cursor = conn.cursor()

    search = f"%{query.lower()}%"

    cursor.execute("""
        SELECT *
        FROM jmo_solutions
        WHERE LOWER(title) LIKE ?
           OR LOWER(keywords) LIKE ?
           OR LOWER(error_code) LIKE ?
           OR LOWER(problem) LIKE ?
        ORDER BY id DESC
    """, (
        search,
        search,
        search,
        search
    ))

    results = cursor.fetchall()

    conn.close()

    return results


# =========================
# INIT DATABASE
# =========================

init_db()
