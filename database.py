import sqlite3
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime, timedelta

from config import DATABASE_PATH, REQUEST_TIMEOUT_MINUTES


# ---------------- helpers ----------------
def _connect():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    return column in cols


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col_def: str, col_name: str):
    if not _table_has_column(conn, table, col_name):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


# ---------------- schema + migrations ----------------
def init_db():
    """Initialize DB tables and run lightweight migrations."""
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            role TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            document_type TEXT,
            document_path TEXT,
            vehicle_type TEXT,
            vehicle_seats INTEGER,
            vehicle_year_model TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP,
            approved_by INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS riders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            pickup_lat REAL NOT NULL,
            pickup_lon REAL NOT NULL,
            dropoff_lat REAL NOT NULL,
            dropoff_lon REAL NOT NULL,
            ride_time TEXT NOT NULL,
            passengers INTEGER NOT NULL,
            status TEXT DEFAULT 'searching',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            matched_at TIMESTAMP,
            matched_driver_id INTEGER,
            UNIQUE(user_id, status)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            start_lat REAL NOT NULL,
            start_lon REAL NOT NULL,
            end_lat REAL NOT NULL,
            end_lon REAL NOT NULL,
            ride_time TEXT NOT NULL,
            available_seats INTEGER NOT NULL,
            status TEXT DEFAULT 'available',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, status)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rider_id INTEGER NOT NULL,
            driver_id INTEGER NOT NULL,
            rider_user_id INTEGER NOT NULL,
            driver_user_id INTEGER NOT NULL,
            trip_id INTEGER,
            seats_used INTEGER DEFAULT 1,
            fare_total REAL,
            surge_multiplier REAL,
            status TEXT DEFAULT 'matched',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (rider_id) REFERENCES riders (id),
            FOREIGN KEY (driver_id) REFERENCES drivers (id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            driver_user_id INTEGER NOT NULL,
            total_seats INTEGER NOT NULL,
            seats_filled INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            ride_time TEXT NOT NULL,
            start_lat REAL NOT NULL,
            start_lon REAL NOT NULL,
            end_lat REAL NOT NULL,
            end_lon REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trip_passengers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            rider_user_id INTEGER NOT NULL,
            rider_id INTEGER NOT NULL,
            passengers INTEGER NOT NULL,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trip_id) REFERENCES trips (id),
            FOREIGN KEY (rider_id) REFERENCES riders (id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            rater_user_id INTEGER NOT NULL,
            ratee_user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trip_id, rater_user_id, ratee_user_id)
        )
        """
    )

    # ---- migrations for existing DBs ----
    _add_column_if_missing(conn, "riders", "is_priority INTEGER DEFAULT 0", "is_priority")
    _add_column_if_missing(conn, "riders", "surge_multiplier REAL DEFAULT 1.0", "surge_multiplier")
    _add_column_if_missing(conn, "riders", "fare_total REAL", "fare_total")
    _add_column_if_missing(conn, "riders", "trip_id INTEGER", "trip_id")
    _add_column_if_missing(conn, "riders", "reminder_sent INTEGER DEFAULT 0", "reminder_sent")

    _add_column_if_missing(conn, "drivers", "trip_id INTEGER", "trip_id")

    conn.commit()
    conn.close()


# ---------------- request/offer gating ----------------
def has_active_rider_request(user_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM riders WHERE user_id=? AND status='searching' LIMIT 1", (user_id,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


def has_active_driver_offer(user_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM drivers WHERE user_id=? AND status='available' LIMIT 1", (user_id,))
    ok = cur.fetchone() is not None
    conn.close()
    return ok


def expire_old_requests() -> Dict[str, int]:
    """
    Expire searching riders and available drivers older than REQUEST_TIMEOUT_MINUTES.
    (We mark status='expired' instead of deleting, so it’s auditable.)
    """
    conn = _connect()
    cur = conn.cursor()

    # Riders
    cur.execute(
        """
        UPDATE riders
        SET status='expired'
        WHERE status='searching'
          AND ((julianday('now') - julianday(created_at)) * 24 * 60) >= ?
        """,
        (REQUEST_TIMEOUT_MINUTES,),
    )
    expired_riders = cur.rowcount

    # Drivers
    cur.execute(
        """
        UPDATE drivers
        SET status='expired'
        WHERE status='available'
          AND ((julianday('now') - julianday(created_at)) * 24 * 60) >= ?
        """,
        (REQUEST_TIMEOUT_MINUTES,),
    )
    expired_drivers = cur.rowcount

    conn.commit()
    conn.close()
    return {"expired_riders": expired_riders, "expired_drivers": expired_drivers}


# ---------------- users ----------------
def register_user(
    user_id: int,
    username: str,
    first_name: str,
    role: str,
    name: str,
    phone: str,
    document_type: str = None,
    document_path: str = None,
    vehicle_type: str = None,
    vehicle_seats: int = None,
    vehicle_year_model: str = None,
) -> int:
    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE user_id = ?", (user_id,))
    existing = cur.fetchone()
    if existing:
        cur.execute(
            """
            UPDATE users SET role=?, name=?, phone=?, document_type=?,
                document_path=?, vehicle_type=?, vehicle_seats=?,
                vehicle_year_model=?, status='pending', username=?, first_name=?
            WHERE user_id=?
            """,
            (
                role,
                name,
                phone,
                document_type,
                document_path,
                vehicle_type,
                vehicle_seats,
                vehicle_year_model,
                username,
                first_name,
                user_id,
            ),
        )
        rid = existing[0]
    else:
        cur.execute(
            """
            INSERT INTO users (user_id, username, first_name, role, name, phone,
                              document_type, document_path, vehicle_type, vehicle_seats,
                              vehicle_year_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                first_name,
                role,
                name,
                phone,
                document_type,
                document_path,
                vehicle_type,
                vehicle_seats,
                vehicle_year_model,
            ),
        )
        rid = cur.lastrowid

    conn.commit()
    conn.close()
    return rid


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_status(user_id: int) -> Optional[str]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT status FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def is_user_approved(user_id: int) -> bool:
    return get_user_status(user_id) == "approved"


def get_pending_registrations() -> List[Dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE status='pending' ORDER BY created_at")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def approve_user(user_id: int, admin_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users SET status='approved', approved_at=CURRENT_TIMESTAMP, approved_by=?
        WHERE user_id=? AND status='pending'
        """,
        (admin_id, user_id),
    )
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def reject_user(user_id: int, admin_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users SET status='rejected', approved_at=CURRENT_TIMESTAMP, approved_by=?
        WHERE user_id=? AND status='pending'
        """,
        (admin_id, user_id),
    )
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def get_all_users() -> List[Dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------- riders/drivers (active) ----------------
def add_rider(
    user_id: int,
    username: str,
    first_name: str,
    pickup_lat: float,
    pickup_lon: float,
    dropoff_lat: float,
    dropoff_lon: float,
    ride_time: str,
    passengers: int,
    is_priority: int = 0,
    surge_multiplier: float = 1.0,
    fare_total: float = None,
) -> int:
    conn = _connect()
    cur = conn.cursor()

    # ✅ ENFORCE: no new request while searching exists
    cur.execute("SELECT 1 FROM riders WHERE user_id=? AND status='searching' LIMIT 1", (user_id,))
    if cur.fetchone() is not None:
        conn.close()
        raise ValueError("Active rider request already exists")

    cur.execute(
        """
        INSERT INTO riders (user_id, username, first_name, pickup_lat, pickup_lon, dropoff_lat, dropoff_lon,
                            ride_time, passengers, is_priority, surge_multiplier, fare_total)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            username,
            first_name,
            pickup_lat,
            pickup_lon,
            dropoff_lat,
            dropoff_lon,
            ride_time,
            passengers,
            is_priority,
            surge_multiplier,
            fare_total,
        ),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def add_driver(
    user_id: int,
    username: str,
    first_name: str,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    ride_time: str,
    available_seats: int,
) -> int:
    conn = _connect()
    cur = conn.cursor()

    # ✅ ENFORCE: no new offer while available exists
    cur.execute("SELECT 1 FROM drivers WHERE user_id=? AND status='available' LIMIT 1", (user_id,))
    if cur.fetchone() is not None:
        conn.close()
        raise ValueError("Active driver offer already exists")

    cur.execute(
        """
        INSERT INTO drivers (user_id, username, first_name, start_lat, start_lon, end_lat, end_lon, ride_time, available_seats)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, username, first_name, start_lat, start_lon, end_lat, end_lon, ride_time, available_seats),
    )
    did = cur.lastrowid
    conn.commit()
    conn.close()
    return did


def get_searching_riders() -> List[Tuple]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, username, first_name, pickup_lat, pickup_lon,
               dropoff_lat, dropoff_lon, ride_time, passengers, created_at,
               is_priority, surge_multiplier, fare_total
        FROM riders WHERE status='searching'
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_searching_riders_sorted() -> List[Tuple]:
    """
    Priority riders first, then older requests first.
    """
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, username, first_name, pickup_lat, pickup_lon,
               dropoff_lat, dropoff_lon, ride_time, passengers, created_at,
               is_priority, surge_multiplier, fare_total
        FROM riders
        WHERE status='searching'
        ORDER BY is_priority DESC, created_at ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_available_drivers() -> List[Tuple]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, username, first_name, start_lat, start_lon,
               end_lat, end_lon, ride_time, available_seats, created_at, trip_id
        FROM drivers WHERE status='available'
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_rider_by_user_id(user_id: int) -> Optional[Tuple]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, username, first_name, pickup_lat, pickup_lon,
               dropoff_lat, dropoff_lon, ride_time, passengers, created_at,
               is_priority, surge_multiplier, fare_total
        FROM riders WHERE user_id=? AND status='searching'
        """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_driver_by_user_id(user_id: int) -> Optional[Tuple]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, username, first_name, start_lat, start_lon,
               end_lat, end_lon, ride_time, available_seats, created_at, trip_id
        FROM drivers WHERE user_id=? AND status='available'
        """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def mark_rider_matched(rider_id: int, driver_user_id: int, trip_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE riders SET status='matched', matched_driver_id=?, matched_at=CURRENT_TIMESTAMP, trip_id=?
        WHERE id=?
        """,
        (driver_user_id, trip_id, rider_id),
    )
    conn.commit()
    conn.close()


def decrement_driver_seats(driver_id: int, used: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE drivers SET available_seats = MAX(available_seats - ?, 0) WHERE id=?",
        (used, driver_id),
    )
    cur.execute("SELECT available_seats FROM drivers WHERE id=?", (driver_id,))
    left = cur.fetchone()[0]
    if left <= 0:
        cur.execute("UPDATE drivers SET status='matched' WHERE id=?", (driver_id,))
    conn.commit()
    conn.close()


def cancel_rider_request(user_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM riders WHERE user_id=? AND status='searching'", (user_id,))
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def cancel_driver_offer(user_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM drivers WHERE user_id=? AND status='available'", (user_id,))
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ok


# ---------------- trips (multi-passenger) ----------------
def create_trip(driver_id: int, driver_user_id: int, total_seats: int, ride_time: str,
                start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> int:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO trips (driver_id, driver_user_id, total_seats, ride_time, start_lat, start_lon, end_lat, end_lon)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (driver_id, driver_user_id, total_seats, ride_time, start_lat, start_lon, end_lat, end_lon),
    )
    trip_id = cur.lastrowid
    cur.execute("UPDATE drivers SET trip_id=? WHERE id=?", (trip_id, driver_id))
    conn.commit()
    conn.close()
    return trip_id


def get_active_trip_for_driver(driver_user_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM trips WHERE driver_user_id=? AND status='active' ORDER BY created_at DESC LIMIT 1", (driver_user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_trip_by_id(trip_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM trips WHERE id=?", (trip_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def add_passenger_to_trip(trip_id: int, rider_user_id: int, rider_id: int, passengers: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO trip_passengers (trip_id, rider_user_id, rider_id, passengers)
        VALUES (?, ?, ?, ?)
        """,
        (trip_id, rider_user_id, rider_id, passengers),
    )
    cur.execute("UPDATE trips SET seats_filled = seats_filled + ? WHERE id=?", (passengers, trip_id))
    conn.commit()
    conn.close()


def get_trip_passengers(trip_id: int) -> List[Dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT tp.trip_id, tp.rider_user_id, tp.rider_id, tp.passengers, tp.joined_at,
               u.name, u.phone, u.username
        FROM trip_passengers tp
        LEFT JOIN users u ON u.user_id = tp.rider_user_id
        WHERE tp.trip_id=?
        ORDER BY tp.joined_at ASC
        """,
        (trip_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_trip(trip_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("UPDATE trips SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'", (trip_id,))
    ok = cur.rowcount > 0
    cur.execute("UPDATE matches SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE trip_id=? AND status='matched'", (trip_id,))
    conn.commit()
    conn.close()
    return ok


# ---------------- matches ----------------
def create_match(rider_id: int, driver_id: int, rider_user_id: int, driver_user_id: int,
                 trip_id: int, seats_used: int, fare_total: float, surge_multiplier: float):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO matches (rider_id, driver_id, rider_user_id, driver_user_id, trip_id, seats_used, fare_total, surge_multiplier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (rider_id, driver_id, rider_user_id, driver_user_id, trip_id, seats_used, fare_total, surge_multiplier),
    )
    conn.commit()
    conn.close()


# ---------------- reminders ----------------
def get_riders_near_timeout(minutes_left: int = 5) -> List[Dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM riders
        WHERE status='searching'
          AND reminder_sent=0
          AND ((julianday('now') - julianday(created_at)) * 24 * 60) >= ?
        """,
        (max(0, REQUEST_TIMEOUT_MINUTES - minutes_left),),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_rider_reminded(rider_id: int):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("UPDATE riders SET reminder_sent=1 WHERE id=?", (rider_id,))
    conn.commit()
    conn.close()


# ---------------- ratings ----------------
def add_rating(trip_id: int, rater_user_id: int, ratee_user_id: int, rating: int, comment: str = ""):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO ratings (trip_id, rater_user_id, ratee_user_id, rating, comment)
        VALUES (?, ?, ?, ?, ?)
        """,
        (trip_id, rater_user_id, ratee_user_id, rating, comment or ""),
    )
    conn.commit()
    conn.close()


def has_rating(trip_id: int, rater_user_id: int, ratee_user_id: int) -> bool:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM ratings WHERE trip_id=? AND rater_user_id=? AND ratee_user_id=? LIMIT 1",
        (trip_id, rater_user_id, ratee_user_id),
    )
    ok = cur.fetchone() is not None
    conn.close()
    return ok


def get_user_rating_summary(user_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), AVG(rating) FROM ratings WHERE ratee_user_id=?", (user_id,))
    count, avg = cur.fetchone()
    conn.close()
    if not count:
        return None
    return {"count": int(count), "avg_rating": round(float(avg or 0), 2)}


# ---------------- history ----------------
def get_user_trip_history(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT DISTINCT t.*
        FROM trips t
        LEFT JOIN trip_passengers tp ON tp.trip_id = t.id
        WHERE t.driver_user_id = ?
           OR tp.rider_user_id = ?
        ORDER BY t.created_at DESC
        LIMIT ?
        """,
        (user_id, user_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------- reports ----------------
def get_registration_stats() -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users WHERE status='pending'")
    pending = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE status='approved'")
    approved = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE status='rejected'")
    rejected = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE role='driver' AND status='approved'")
    drivers = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM users WHERE role='passenger' AND status='approved'")
    passengers = cur.fetchone()[0]

    conn.close()
    return {
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "total_drivers": drivers,
        "total_passengers": passengers,
    }


def get_trip_stats() -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM matches")
    total_matches = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM trips")
    total_trips = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM trips WHERE status='active'")
    active_trips = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM trips WHERE status='completed'")
    completed_trips = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM riders WHERE status='searching'")
    riders_waiting = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM drivers WHERE status='available'")
    drivers_available = cur.fetchone()[0]

    conn.close()
    return {
        "total_matches": total_matches,
        "total_trips": total_trips,
        "active_trips": active_trips,
        "completed_trips": completed_trips,
        "riders_waiting": riders_waiting,
        "drivers_available": drivers_available,
    }


def get_seat_utilization() -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT SUM(seats_used) FROM matches")
    total_seats_used = cur.fetchone()[0] or 0

    cur.execute("SELECT SUM(total_seats) FROM trips")
    total_seats_offered = cur.fetchone()[0] or 0

    cur.execute("SELECT SUM(seats_filled) FROM trips")
    total_seats_filled = cur.fetchone()[0] or 0

    conn.close()

    util = 0.0
    if total_seats_offered > 0:
        util = round((total_seats_filled / total_seats_offered) * 100, 1)

    return {
        "total_seats_used": total_seats_used,
        "total_seats_offered": total_seats_offered,
        "total_seats_filled": total_seats_filled,
        "utilization_rate": util,
    }


def get_waiting_time_stats() -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT AVG((julianday(matched_at) - julianday(created_at)) * 24 * 60)
        FROM riders WHERE status='matched' AND matched_at IS NOT NULL
        """
    )
    avg_wait = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*), AVG((julianday('now') - julianday(created_at)) * 24 * 60)
        FROM riders WHERE status='searching'
        """
    )
    cnt, current_avg = cur.fetchone()
    conn.close()

    return {
        "avg_wait_minutes": round(avg_wait, 1) if avg_wait else 0,
        "currently_waiting": cnt,
        "current_avg_wait_minutes": round(current_avg, 1) if current_avg else 0,
    }