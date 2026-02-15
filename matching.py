from __future__ import annotations

from geopy.distance import geodesic
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any

from config import (
    MATCH_RADIUS_KM,
    MATCH_TIME_WINDOW_MINUTES,
    FARE_BASE,
    FARE_PER_KM,
    FARE_CURRENCY,
    ENABLE_SURGE_PRICING,
    SURGE_MAX_MULTIPLIER,
    SURGE_MIN_MULTIPLIER,
    PRIORITY_FEE,
    ENABLE_PRIORITY_MATCHING,
)
import database as db


# Rider tuple format (from db.get_searching_riders_sorted / get_rider_by_user_id):
# (id, user_id, username, first_name, pickup_lat, pickup_lon, dropoff_lat, dropoff_lon, ride_time,
#  passengers, created_at, is_priority, surge_multiplier, fare_total)
#
# Driver tuple format:
# (id, user_id, username, first_name, start_lat, start_lon, end_lat, end_lon, ride_time,
#  available_seats, created_at, trip_id)


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return geodesic((lat1, lon1), (lat2, lon2)).kilometers


def calculate_fare(distance_km: float) -> float:
    return FARE_BASE + (FARE_PER_KM * distance_km)


def calculate_fare_per_passenger(total_fare: float, passengers: int) -> float:
    if passengers <= 0:
        return total_fare
    return round(total_fare / passengers, 2)


def format_money(amount: float) -> str:
    return f"{FARE_CURRENCY}{amount:.2f}"


def format_fare(amount: float) -> str:
    return format_money(amount)


def parse_time(time_str: str) -> datetime:
    today = datetime.now().date()
    time_parts = time_str.split(":")
    hour = int(time_parts[0])
    minute = int(time_parts[1]) if len(time_parts) > 1 else 0
    return datetime.combine(today, datetime.strptime(f"{hour}:{minute}", "%H:%M").time())


def times_compatible(rider_time: str, driver_time: str) -> bool:
    try:
        rider_dt = parse_time(rider_time)
        driver_dt = parse_time(driver_time)
        diff = abs((rider_dt - driver_dt).total_seconds() / 60)
        return diff <= MATCH_TIME_WINDOW_MINUTES
    except Exception:
        return False


def _surge_multiplier(ride_time: Optional[str] = None) -> float:
    """
    Simple surge model: demand/supply ratio -> multiplier.
    Uses current searching riders + available drivers (optionally filtered by time).
    """
    if not ENABLE_SURGE_PRICING:
        return 1.0

    riders = db.get_searching_riders()
    drivers = db.get_available_drivers()

    if ride_time:
        riders = [r for r in riders if r[8] == ride_time]
        drivers = [d for d in drivers if d[8] == ride_time]

    demand = len(riders)
    supply = max(1, len(drivers))
    ratio = demand / supply  # 0..∞

    # Convert ratio to multiplier: 1.0 at ratio<=1, scales to max at high ratios
    if ratio <= 1.0:
        mult = SURGE_MIN_MULTIPLIER
    else:
        # Smooth growth with cap
        mult = 1.0 + min(ratio - 1.0, SURGE_MAX_MULTIPLIER - 1.0)

    mult = max(SURGE_MIN_MULTIPLIER, min(SURGE_MAX_MULTIPLIER, mult))
    return round(mult, 2)


def get_fare_estimate(
    pickup_lat: float,
    pickup_lon: float,
    dropoff_lat: float,
    dropoff_lon: float,
    passengers: int,
    ride_time: Optional[str] = None,
) -> Dict[str, Any]:
    distance = calculate_distance(pickup_lat, pickup_lon, dropoff_lat, dropoff_lon)
    base_fare = calculate_fare(distance)
    surge_mult = _surge_multiplier(ride_time=ride_time) if ENABLE_SURGE_PRICING else 1.0
    total_fare = round(base_fare * surge_mult, 2)
    fare_per_passenger = calculate_fare_per_passenger(total_fare, passengers)
    return {
        "distance_km": round(distance, 2),
        "total_fare": total_fare,
        "fare_per_passenger": fare_per_passenger,
        "formatted_total": format_fare(total_fare),
        "formatted_per_passenger": format_fare(fare_per_passenger),
        "surge_multiplier": surge_mult,
    }


def _driver_matches_rider(r: Tuple, d: Tuple) -> bool:
    rider_time = r[8]
    passengers = int(r[9])

    driver_time = d[8]
    available_seats = int(d[9])

    if available_seats < passengers:
        return False

    if not times_compatible(rider_time, driver_time):
        return False

    rider_pickup_lat, rider_pickup_lon = float(r[4]), float(r[5])
    rider_drop_lat, rider_drop_lon = float(r[6]), float(r[7])

    driver_start_lat, driver_start_lon = float(d[4]), float(d[5])
    driver_end_lat, driver_end_lon = float(d[6]), float(d[7])

    pickup_distance = calculate_distance(rider_pickup_lat, rider_pickup_lon, driver_start_lat, driver_start_lon)
    if pickup_distance > MATCH_RADIUS_KM:
        return False

    dropoff_distance = calculate_distance(rider_drop_lat, rider_drop_lon, driver_end_lat, driver_end_lon)
    if dropoff_distance > MATCH_RADIUS_KM:
        return False

    return True


def find_match_for_rider(rider: Tuple, drivers_cache: Optional[List[Tuple]] = None) -> Optional[Tuple]:
    """
    Choose best driver for rider.
    Currently: first match from drivers list (ordered by oldest offer).
    """
    drivers = drivers_cache if drivers_cache is not None else db.get_available_drivers()

    # Prefer drivers with more seats (helps multi-passenger)
    drivers_sorted = sorted(drivers, key=lambda d: (-int(d[9]), d[10]))

    for d in drivers_sorted:
        if _driver_matches_rider(rider, d):
            return d
    return None


def find_match_for_driver(driver: Tuple, riders_cache: Optional[List[Tuple]] = None) -> Optional[Tuple]:
    """
    Choose best rider for driver.
    Priority riders first, then oldest requests first.
    """
    riders = riders_cache if riders_cache is not None else db.get_searching_riders_sorted()

    # riders already sorted by priority desc, created_at asc
    for r in riders:
        if _driver_matches_rider(r, driver):
            return r
    return None


def process_match(rider: Tuple, driver: Tuple) -> Dict[str, Any]:
    """
    Records match, creates/updates trip, updates seats, and returns details for notifications.
    """
    rider_id = int(rider[0])
    rider_user_id = int(rider[1])
    rider_username = rider[2] or ""
    rider_first_name = rider[3] or "Rider"
    rider_pickup = (float(rider[4]), float(rider[5]))
    rider_dropoff = (float(rider[6]), float(rider[7]))
    rider_time = rider[8]
    passengers = int(rider[9])
    is_priority = int(rider[11] or 0)

    driver_id = int(driver[0])
    driver_user_id = int(driver[1])
    driver_username = driver[2] or ""
    driver_first_name = driver[3] or "Driver"
    driver_start = (float(driver[4]), float(driver[5]))
    driver_end = (float(driver[6]), float(driver[7]))
    driver_time = driver[8]
    seats_left_before = int(driver[9])
    trip_id_existing = driver[11]

    # Trip: create if missing
    if trip_id_existing:
        trip_id = int(trip_id_existing)
    else:
        trip_id = db.create_trip(
            driver_id=driver_id,
            driver_user_id=driver_user_id,
            total_seats=seats_left_before,
            ride_time=driver_time,
            start_lat=driver_start[0],
            start_lon=driver_start[1],
            end_lat=driver_end[0],
            end_lon=driver_end[1],
        )

    # Fare: rider has fare_total stored; fallback compute
    fare = get_fare_estimate(rider_pickup[0], rider_pickup[1], rider_dropoff[0], rider_dropoff[1], passengers, ride_time=rider_time)
    # Priority fee is an add-on charged to rider (optional)
    if ENABLE_PRIORITY_MATCHING and is_priority:
        fare["total_fare"] = round(float(fare["total_fare"]) + float(PRIORITY_FEE), 2)
        fare["fare_per_passenger"] = calculate_fare_per_passenger(fare["total_fare"], passengers)
        fare["formatted_total"] = format_fare(fare["total_fare"])
        fare["formatted_per_passenger"] = format_fare(fare["fare_per_passenger"])

    # DB updates
    db.add_passenger_to_trip(trip_id, rider_user_id, rider_id, passengers)
    db.mark_rider_matched(rider_id, driver_user_id, trip_id)
    db.decrement_driver_seats(driver_id, passengers)
    db.create_match(
        rider_id=rider_id,
        driver_id=driver_id,
        rider_user_id=rider_user_id,
        driver_user_id=driver_user_id,
        trip_id=trip_id,
        seats_used=passengers,
        fare_total=float(fare["total_fare"]),
        surge_multiplier=float(fare.get("surge_multiplier", 1.0)),
    )

    # seats left after
    updated_driver = db.get_driver_by_user_id(driver_user_id)
    seats_left_after = int(updated_driver[9]) if updated_driver else max(0, seats_left_before - passengers)

    return {
        "trip_id": trip_id,
        "rider_user_id": rider_user_id,
        "rider_username": rider_username,
        "rider_first_name": rider_first_name,
        "rider_pickup": rider_pickup,
        "rider_dropoff": rider_dropoff,
        "rider_time": rider_time,
        "passengers": passengers,
        "driver_user_id": driver_user_id,
        "driver_username": driver_username,
        "driver_first_name": driver_first_name,
        "driver_start": driver_start,
        "driver_end": driver_end,
        "driver_time": driver_time,
        "available_seats": seats_left_before,
        "driver_seats_left": seats_left_after,
        "fare": fare,
    }
