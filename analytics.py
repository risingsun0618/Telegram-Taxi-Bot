import sqlite3
from typing import List, Dict, Any
from datetime import datetime, timedelta

from config import DATABASE_PATH


def _connect():
    conn = sqlite3.connect(DATABASE_PATH)
    return conn


def get_peak_times(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Peak times based on count of riders + drivers created for each ride_time.
    """
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # union riders/drivers by ride_time
    cur.execute(
        """
        SELECT ride_time, SUM(cnt) as count
        FROM (
            SELECT ride_time, COUNT(*) AS cnt FROM riders GROUP BY ride_time
            UNION ALL
            SELECT ride_time, COUNT(*) AS cnt FROM drivers GROUP BY ride_time
        )
        GROUP BY ride_time
        ORDER BY count DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_daily_trends(days: int = 7) -> List[Dict[str, Any]]:
    """
    Trips created per day.
    """
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT DATE(created_at) AS date, COUNT(*) AS count
        FROM trips
        WHERE created_at >= datetime('now', ?)
        GROUP BY DATE(created_at)
        ORDER BY date DESC
        """,
        (f"-{days} days",),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_current_supply_demand() -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM riders WHERE status='searching'")
    riders_waiting = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM drivers WHERE status='available'")
    drivers_available = cur.fetchone()[0]
    conn.close()
    return {"riders_waiting": riders_waiting, "drivers_available": drivers_available}
