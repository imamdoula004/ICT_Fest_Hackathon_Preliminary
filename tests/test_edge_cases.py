"""Comprehensive edge-case and concurrency tests for CoWork API.

Exercises all business rules and verifies the 17 bug fixes.
"""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import Booking, RefundLog

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_db():
    # Clear the database tables between tests to ensure test isolation.
    db = SessionLocal()
    try:
        db.query(RefundLog).delete()
        db.query(Booking).delete()
        # Delete users and orgs if needed, or rely on unique names per test.
        db.commit()
    finally:
        db.close()


def _future(hours: int, tz_offset: str = None) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    dt = dt.replace(minute=0, second=0, microsecond=0)
    if tz_offset:
        # e.g., '+02:00' -> parses to timezone
        offset_hours = int(tz_offset[1:3])
        offset_mins = int(tz_offset[4:6])
        if tz_offset[0] == "-":
            offset_hours = -offset_hours
            offset_mins = -offset_mins
        tz = timezone(timedelta(hours=offset_hours, minutes=offset_mins))
        dt = dt.astimezone(tz)
        return dt.isoformat()
    return dt.replace(tzinfo=timezone.utc).isoformat()


def test_register_duplicate():
    org = f"org-dup-{datetime.now().timestamp()}"
    reg1 = client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    assert reg1.status_code == 201

    reg2 = client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    assert reg2.status_code == 409
    assert reg2.json()["code"] == "USERNAME_TAKEN"


def test_refresh_single_use():
    org = f"org-ref-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    assert login.status_code == 200
    ref_token = login.json()["refresh_token"]

    # First refresh: Should succeed
    ref1 = client.post("/auth/refresh", json={"refresh_token": ref_token})
    assert ref1.status_code == 200
    assert "access_token" in ref1.json()

    # Second refresh using same token: Should fail (401)
    ref2 = client.post("/auth/refresh", json={"refresh_token": ref_token})
    assert ref2.status_code == 401


def test_datetime_timezone():
    org = f"org-tz-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    room = client.post("/rooms", json={"name": "Room A", "capacity": 2, "hourly_rate_cents": 1000}, headers=headers)
    room_id = room.json()["id"]

    # Book with offset +02:00
    start = _future(24, "+02:00")
    end = _future(26, "+02:00")
    
    booking = client.post("/bookings", json={"room_id": room_id, "start_time": start, "end_time": end}, headers=headers)
    assert booking.status_code == 201
    
    # Assert return values are converted to UTC (with explicit 'Z' or '+00:00')
    res_start = booking.json()["start_time"]
    assert "+00:00" in res_start or "Z" in res_start or res_start.endswith("+00:00")
    
    # Check that we parsed the offset correctly (e.g. +02:00 shifted back 2 hours)
    start_dt = datetime.fromisoformat(start)
    start_utc = start_dt.astimezone(timezone.utc)
    assert res_start.replace("Z", "+00:00") == start_utc.isoformat().replace("Z", "+00:00")


def test_booking_invalid_duration_and_past():
    org = f"org-dur-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    room = client.post("/rooms", json={"name": "Room A", "capacity": 2, "hourly_rate_cents": 1000}, headers=headers)
    room_id = room.json()["id"]

    # 1. 0 hours duration
    start = _future(5)
    booking_0h = client.post("/bookings", json={"room_id": room_id, "start_time": start, "end_time": start}, headers=headers)
    assert booking_0h.status_code == 400
    assert booking_0h.json()["code"] == "INVALID_BOOKING_WINDOW"

    # 2. Negative duration
    booking_neg = client.post("/bookings", json={"room_id": room_id, "start_time": _future(6), "end_time": _future(5)}, headers=headers)
    assert booking_neg.status_code == 400
    assert booking_neg.json()["code"] == "INVALID_BOOKING_WINDOW"

    # 3. Start in the past
    past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    past_end = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    booking_past = client.post("/bookings", json={"room_id": room_id, "start_time": past_start, "end_time": past_end}, headers=headers)
    assert booking_past.status_code == 400
    assert booking_past.json()["code"] == "INVALID_BOOKING_WINDOW"


def test_booking_quota():
    org = f"org-quota-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    room = client.post("/rooms", json={"name": "Room A", "capacity": 2, "hourly_rate_cents": 1000}, headers=headers)
    room_id = room.json()["id"]

    # Create 3 confirmed bookings in the next 24h window
    for h in [2, 4, 6]:
        b = client.post("/bookings", json={"room_id": room_id, "start_time": _future(h), "end_time": _future(h+1)}, headers=headers)
        assert b.status_code == 201

    # 4th booking should exceed quota (limit is 3)
    b4 = client.post("/bookings", json={"room_id": room_id, "start_time": _future(8), "end_time": _future(9)}, headers=headers)
    assert b4.status_code == 409
    assert b4.json()["code"] == "QUOTA_EXCEEDED"


def test_cancellation_notice_and_rounding():
    org = f"org-cancel-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Hourly rate is odd so 50% refund ends in .5 cents (e.g. 1005 cents * 1 hour = 1005 cents)
    room = client.post("/rooms", json={"name": "Room A", "capacity": 2, "hourly_rate_cents": 1005}, headers=headers)
    room_id = room.json()["id"]

    # Booking 1: Notice >= 48 hours (100% refund)
    b1 = client.post("/bookings", json={"room_id": room_id, "start_time": _future(50), "end_time": _future(51)}, headers=headers)
    b1_id = b1.json()["id"]
    cancel1 = client.post(f"/bookings/{b1_id}/cancel", headers=headers)
    assert cancel1.status_code == 200
    assert cancel1.json()["refund_percent"] == 100
    assert cancel1.json()["refund_amount_cents"] == 1005

    # Booking 2: 24h <= Notice < 48h (50% refund, rounding half-cents up)
    # Notice = 30 hours. Refund = 50% of 1005 = 502.5 -> rounds up to 503
    b2 = client.post("/bookings", json={"room_id": room_id, "start_time": _future(30), "end_time": _future(31)}, headers=headers)
    b2_id = b2.json()["id"]
    cancel2 = client.post(f"/bookings/{b2_id}/cancel", headers=headers)
    assert cancel2.status_code == 200
    assert cancel2.json()["refund_percent"] == 50
    assert cancel2.json()["refund_amount_cents"] == 503

    # Booking 3: Notice < 24 hours (0% refund)
    # Notice = 10 hours.
    b3 = client.post("/bookings", json={"room_id": room_id, "start_time": _future(10), "end_time": _future(11)}, headers=headers)
    b3_id = b3.json()["id"]
    cancel3 = client.post(f"/bookings/{b3_id}/cancel", headers=headers)
    assert cancel3.status_code == 200
    assert cancel3.json()["refund_percent"] == 0
    assert cancel3.json()["refund_amount_cents"] == 0


def test_admin_export_cross_org():
    org_a = f"org-a-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org_a, "username": "alice", "password": "password"})
    login_a = client.post("/auth/login", json={"org_name": org_a, "username": "alice", "password": "password"})
    headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}

    org_b = f"org-b-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org_b, "username": "bob", "password": "password"})
    login_b = client.post("/auth/login", json={"org_name": org_b, "username": "bob", "password": "password"})
    headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}

    # Org B creates a room
    room_b = client.post("/rooms", json={"name": "Focus Room B", "capacity": 2, "hourly_rate_cents": 1000}, headers=headers_b)
    room_b_id = room_b.json()["id"]

    # Admin of Org A tries to export Org B's room bookings
    export_req = client.get(f"/admin/export?room_id={room_b_id}", headers=headers_a)
    assert export_req.status_code == 404
    assert export_req.json()["code"] == "ROOM_NOT_FOUND"


def test_bookings_pagination_limit_sort():
    org = f"org-pag-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    room = client.post("/rooms", json={"name": "Room A", "capacity": 2, "hourly_rate_cents": 1000}, headers=headers)
    room_id = room.json()["id"]

    # Create 3 bookings starting at +10h, +5h, +15h (in unsorted order)
    client.post("/bookings", json={"room_id": room_id, "start_time": _future(10), "end_time": _future(11)}, headers=headers)
    client.post("/bookings", json={"room_id": room_id, "start_time": _future(5), "end_time": _future(6)}, headers=headers)
    client.post("/bookings", json={"room_id": room_id, "start_time": _future(15), "end_time": _future(16)}, headers=headers)

    # Fetch page=1, limit=2
    list_req = client.get("/bookings?page=1&limit=2", headers=headers)
    assert list_req.status_code == 200
    res = list_req.json()
    assert res["total"] == 3
    assert len(res["items"]) == 2
    assert res["page"] == 1
    assert res["limit"] == 2

    # Verify they are sorted ascending by start_time: +5h, then +10h
    item_starts = [i["start_time"] for i in res["items"]]
    assert _future(5)[:16] in item_starts[0]
    assert _future(10)[:16] in item_starts[1]


def test_concurrency_double_booking():
    org = f"org-c-db-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    room = client.post("/rooms", json={"name": "Room A", "capacity": 2, "hourly_rate_cents": 1000}, headers=headers)
    room_id = room.json()["id"]

    # 10 threads trying to book the exact same slot concurrently
    start = _future(20)
    end = _future(21)
    
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(
                client.post,
                "/bookings",
                json={"room_id": room_id, "start_time": start, "end_time": end},
                headers=headers
            )
            for _ in range(10)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly 1 booking must succeed (201), the rest must fail with 409 ROOM_CONFLICT
    success = [r for r in results if r.status_code == 201]
    conflict = [r for r in results if r.status_code == 409 and r.json()["code"] == "ROOM_CONFLICT"]
    
    assert len(success) == 1
    assert len(conflict) == 9


def test_concurrency_double_cancellation():
    org = f"org-c-dc-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    room = client.post("/rooms", json={"name": "Room A", "capacity": 2, "hourly_rate_cents": 1000}, headers=headers)
    room_id = room.json()["id"]

    # Create one booking
    b = client.post("/bookings", json={"room_id": room_id, "start_time": _future(50), "end_time": _future(51)}, headers=headers)
    b_id = b.json()["id"]

    # Cancel concurrently with 5 threads
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(client.post, f"/bookings/{b_id}/cancel", headers=headers)
            for _ in range(5)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly 1 cancel must succeed (200), rest must return 409 ALREADY_CANCELLED
    success = [r for r in results if r.status_code == 200]
    already_cancelled = [r for r in results if r.status_code == 409 and r.json()["code"] == "ALREADY_CANCELLED"]

    assert len(success) == 1
    assert len(already_cancelled) == 4
    
    # Assert database has exactly 1 RefundLog for this booking
    db = SessionLocal()
    try:
        refunds = db.query(RefundLog).filter(RefundLog.booking_id == b_id).all()
        assert len(refunds) == 1
    finally:
        db.close()


def test_rate_limiting():
    org = f"org-rl-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "bob", "password": "password"})
    login = client.post("/auth/login", json={"org_name": org, "username": "bob", "password": "password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # We send 21 requests to /bookings
    # First 20 will return 400 or 404 (e.g. ROOM_NOT_FOUND since room_id=99999 is invalid)
    # The 21st must return 429 RATE_LIMITED.
    status_codes = []
    for _ in range(21):
        res = client.post("/bookings", json={"room_id": 99999, "start_time": _future(5), "end_time": _future(6)}, headers=headers)
        status_codes.append(res.status_code)

    # First 20 are NOT 429
    assert all(code != 429 for code in status_codes[:20])
    # The 21st is 429
    assert status_codes[20] == 429

