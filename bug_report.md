# Bug Report: CoWork API

This report documents the bugs discovered in the CoWork REST API codebase, including original line numbers, buggy code snippets, why they caused incorrect behaviour, corrected line numbers, and the corrected code snippets.

---

### 1. Datetime Parsing Offset Loss
* **File**: `app/timeutils.py`
* **Original Line(s)**: L12-L13
* **Buggy Code**:
```python
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt
```
* **Why it was buggy**: It stripped the timezone info (e.g., `+02:00`) using `dt.replace(tzinfo=None)` directly, without converting the time to UTC first. This resulted in wrong datetime values stored (e.g., `2026-07-09T18:00:00+02:00` was stored as `18:00:00` instead of `16:00:00` UTC).
* **Corrected Line(s)**: L12-L13
* **Corrected Code**:
```python
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
```
* **How it was fixed**: Converted the `dt` to UTC first using `dt.astimezone(timezone.utc)` before stripping the timezone info.

---

### 2. Token Lifetime Duration
* **File**: `app/auth.py`
* **Original Line(s)**: L50
* **Buggy Code**:
```python
    lifetime = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
```
* **Why it was buggy**: It multiplied the minutes by 60 when passing it to the `minutes` parameter of `timedelta`. Since `ACCESS_TOKEN_EXPIRE_MINUTES` is defined as `15`, this evaluated to 900 minutes (15 hours) instead of 15 minutes (900 seconds), violating Business Rule 8.
* **Corrected Line(s)**: L50
* **Corrected Code**:
```python
    lifetime = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
```
* **How it was fixed**: Changed the parameter multiplier so that the duration evaluates to exactly 15 minutes (900 seconds).

---

### 3. Revoked Access Token Verification
* **File**: `app/auth.py`
* **Original Line(s)**: L97
* **Buggy Code**:
```python
    if payload.get("sub") in _revoked_tokens:
        raise AppError(401, "UNAUTHORIZED", "Token has been revoked")
```
* **Why it was buggy**: It checked `payload.get("sub")` (which is the user ID string) against the set of revoked tokens, but `revoke_access_token` recorded `payload["jti"]` (the unique token ID). Thus, logged-out tokens were not blocked on subsequent requests.
* **Corrected Line(s)**: L105-L106
* **Corrected Code**:
```python
    if is_token_revoked(payload.get("jti")):
        raise AppError(401, "UNAUTHORIZED", "Token has been revoked")
```
* **How it was fixed**: Changed the validation check to check the token's `jti` instead of `sub` against the revoked token set, and created helper lookup functions `is_token_revoked` and `revoke_token`.

---

### 4. Duplicate Username Registration Status Code
* **File**: `app/routers/auth.py`
* **Original Line(s)**: L37-L43
* **Buggy Code**:
```python
    if existing is not None:
        return {
            "user_id": existing.id,
            "org_id": org.id,
            "username": existing.username,
            "role": existing.role,
        }
```
* **Why it was buggy**: If a user tried to register with an existing username inside the same organization, the endpoint returned the existing user details with a `201 Created` status code instead of returning `409 USERNAME TAKEN`.
* **Corrected Line(s)**: L45
* **Corrected Code**:
```python
    if existing is not None:
        raise AppError(409, "USERNAME_TAKEN", "Username already taken within organization")
```
* **How it was fixed**: Enforced raising `AppError(409, "USERNAME_TAKEN", ...)` on duplicate user registration.

---

### 5. Multi-Thread Registration Race Condition
* **File**: `app/routers/auth.py`
* **Original Line(s)**: L26-L30 and L52-L53
* **Buggy Code**:
```python
        org = Organization(name=payload.org_name)
        db.add(org)
        db.commit()
        db.refresh(org)
```
and
```python
    db.add(user)
    db.commit()
    db.refresh(user)
```
* **Why it was buggy**: Under concurrent requests, two threads could check `existing` simultaneously, see it doesn't exist, and proceed to insert the user, triggering an SQLite UniqueConstraint violation and crashing the endpoint with a 500 error.
* **Corrected Line(s)**: L30-L37 and L60-L64
* **Corrected Code**:
```python
        try:
            db.commit()
            db.refresh(org)
        except IntegrityError:
            db.rollback()
            org = db.query(Organization).filter(Organization.name == payload.org_name).first()
            if org is None:
                raise AppError(500, "DATABASE_ERROR", "Organization creation conflict")
            role = "member"
```
and
```python
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise AppError(409, "USERNAME_TAKEN", "Username already taken within organization")
```
* **How it was fixed**: Caught `IntegrityError` from SQLAlchemy on db commits, rolled back the failed session, and raised `USERNAME_TAKEN (409)`.

---

### 6. Refresh Token Reuse
* **File**: `app/routers/auth.py`
* **Original Line(s)**: L81-L93
* **Buggy Code**:
```python
@router.post("/refresh")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    data = decode_token(payload.refresh_token)
    if data.get("type") != "refresh":
        raise AppError(401, "UNAUTHORIZED", "Wrong token type")
    user = db.query(User).filter(User.id == int(data["sub"])).first()
    if user is None:
        raise AppError(401, "UNAUTHORIZED", "Unknown user")
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
        "token_type": "bearer",
    }
```
* **Why it was buggy**: Refresh tokens were not validated for single-use, letting them be reused indefinitely.
* **Corrected Line(s)**: L92-L103
* **Corrected Code**:
```python
    jti = data.get("jti")
    with _refresh_lock:
        if is_token_revoked(jti):
            raise AppError(401, "UNAUTHORIZED", "Token has been revoked")
        revoke_token(jti)
```
* **How it was fixed**: Added a thread-safe check using `is_token_revoked` and called `revoke_token(jti)` immediately on token usage inside a thread lock `_refresh_lock`.

---

### 7. Past Start Time Grace Window
* **File**: `app/routers/bookings.py`
* **Original Line(s)**: L86-L87
* **Buggy Code**:
```python
    if start <= now - timedelta(seconds=300):
        raise AppError(400, "INVALID_BOOKING_WINDOW", "start_time must be in the future")
```
* **Why it was buggy**: It allowed booking slots that started in the past (up to 5 minutes ago) instead of enforcing "strictly in the future at request time - no grace window" per Business Rule 2.
* **Corrected Line(s)**: L90-L91
* **Corrected Code**:
```python
    if start <= now:
        raise AppError(400, "INVALID_BOOKING_WINDOW", "start_time must be in the future")
```
* **How it was fixed**: Changed the boundary check to `start <= now`.

---

### 8. Minimum Booking Duration Validation
* **File**: `app/routers/bookings.py`
* **Original Line(s)**: L93-L94
* **Buggy Code**:
```python
    if duration_hours > MAX_DURATION_HOURS:
        raise AppError(400, "INVALID_BOOKING_WINDOW", "duration out of range")
```
* **Why it was buggy**: The code did not enforce a minimum duration of 1 hour, allowing 0 or negative hours duration.
* **Corrected Line(s)**: L98-L99
* **Corrected Code**:
```python
    if duration_hours < MIN_DURATION_HOURS or duration_hours > MAX_DURATION_HOURS:
        raise AppError(400, "INVALID_BOOKING_WINDOW", "duration out of range")
```
* **How it was fixed**: Enforced `duration_hours < MIN_DURATION_HOURS` alongside maximum checks.

---

### 9. Booking Creation Cache Invalidation
* **File**: `app/routers/bookings.py`
* **Original Line(s)**: L116-L121
* **Buggy Code**:
```python
    db.add(booking)
    db.commit()
    db.refresh(booking)

    stats.record_create(room.id, price_cents)
    cache.invalidate_availability(room.id, start.date().isoformat())
    notifications.notify_created(booking)
```
* **Why it was buggy**: Creating a new booking did not invalidate the usage report cache, causing the admin usage report to return stale data instead of reflecting the current state immediately.
* **Corrected Line(s)**: L124-L130
* **Corrected Code**:
```python
        db.add(booking)
        db.commit()
        db.refresh(booking)

    stats.record_create(room.id, price_cents)
    cache.invalidate_availability(room.id, start.date().isoformat())
    cache.invalidate_report(user.org_id)
    notifications.notify_created(booking)
```
* **How it was fixed**: Added `cache.invalidate_report(user.org_id)` after successful booking creation.

---

### 10. Booking Pagination Offset & Limit & Sort
* **File**: `app/routers/bookings.py`
* **Original Line(s)**: L137-L141
* **Buggy Code**:
```python
    items = (
        base.order_by(Booking.start_time.desc(), Booking.id.asc())
        .offset(page * limit)
        .limit(10)
        .all()
    )
```
* **Why it was buggy**: 
  1. Offset was calculated as `page * limit` which skips the first page.
  2. Limit was hardcoded to `10` instead of using the user-supplied `limit` parameter.
  3. Ordered by `start_time` descending instead of ascending.
* **Corrected Line(s)**: L145-L150
* **Corrected Code**:
```python
    items = (
        base.order_by(Booking.start_time.asc(), Booking.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
```
* **How it was fixed**: Changed sorting to ascending start time, offset formula to `(page - 1) * limit`, and limit to `.limit(limit)`.

---

### 11. Single Booking Start Time Override
* **File**: `app/routers/bookings.py`
* **Original Line(s)**: L166
* **Buggy Code**:
```python
    response = serialize_booking(booking)
    response["start_time"] = iso_utc(booking.created_at)
```
* **Why it was buggy**: It overrode the actual booking start time `start_time` in the response object with the `created_at` timestamp.
* **Corrected Line(s)**: L173
* **Corrected Code**:
```python
    response = serialize_booking(booking)
```
* **How it was fixed**: Deleted the line overriding `start_time`.

---

### 12. Cancellation Notice Windows & Refund Rates
* **File**: `app/routers/bookings.py`
* **Original Line(s)**: L199-L206
* **Buggy Code**:
```python
    notice = booking.start_time - now
    notice_hours = int(notice.total_seconds() // 3600)
    if notice_hours > 48:
        refund_percent = 100
    elif notice >= timedelta(hours=24):
        refund_percent = 50
    else:
        refund_percent = 50
```
* **Why it was buggy**: 
  1. A notice of exactly 48 hours returned 50% instead of 100%.
  2. Notice less than 24 hours fell into the `else` branch which gave 50% instead of 0%.
* **Corrected Line(s)**: L208-L215
* **Corrected Code**:
```python
        notice = booking.start_time - now
        if notice >= timedelta(hours=48):
            refund_percent = 100
        elif notice >= timedelta(hours=24):
            refund_percent = 50
        else:
            refund_percent = 0
```
* **How it was fixed**: Corrected the notice window boundaries and set the `else` refund rate to 0%.

---

### 13. Refund Rounding Math
* **File**: `app/routers/bookings.py` and `app/services/refunds.py`
* **Original Line(s)**: `bookings.py` L208 & `refunds.py` L15-L17
* **Buggy Code** (`bookings.py`):
```python
    refund_amount_cents = round(booking.price_cents * (refund_percent / 100.0))
```
* **Buggy Code** (`refunds.py`):
```python
    dollars = booking.price_cents / 100.0
    refund_dollars = dollars * (percent / 100.0)
    amount_cents = int(refund_dollars * 100)
```
* **Why it was buggy**: Casting float multiplications to integers or using standard `round()` (Banker's rounding) rounded half-cents down (e.g. `52.5` cents became `52` cents) instead of rounding half-cents up to the next integer cent per Business Rule 6.
* **Corrected Line(s)**: `bookings.py` L217-L222 & `refunds.py` L15-L20
* **Corrected Code** (`bookings.py`):
```python
        if refund_percent == 100:
            refund_amount_cents = booking.price_cents
        elif refund_percent == 50:
            refund_amount_cents = (booking.price_cents + 1) // 2
        else:
            refund_amount_cents = 0
```
* **Corrected Code** (`refunds.py`):
```python
    if percent == 100:
        amount_cents = booking.price_cents
    elif percent == 50:
        amount_cents = (booking.price_cents + 1) // 2
    else:
        amount_cents = 0
```
* **How it was fixed**: Used exact integer arithmetic `(price + 1) // 2` to guarantee half-cents are correctly rounded up.

---

### 14. Admin Export Cross-Org Security
* **File**: `app/routers/admin.py`
* **Original Line(s)**: L65-L73
* **Buggy Code**:
```python
@router.get("/export")
def export(
    room_id: int | None = Query(None),
    include_all: bool = Query(False),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    csv_body = generate_export(db, admin.org_id, admin.id, room_id, include_all)
    return Response(content=csv_body, media_type="text/csv")
```
* **Why it was buggy**: The endpoint did not verify whether the passed `room_id` belonged to the admin's organization, allowing cross-org exports.
* **Corrected Line(s)**: L72-L75
* **Corrected Code**:
```python
    if room_id is not None:
        room = db.query(Room).filter(Room.id == room_id, Room.org_id == admin.org_id).first()
        if room is None:
            raise AppError(404, "ROOM_NOT_FOUND", "Room not found")
```
* **How it was fixed**: Validated room organization ownership before proceeding, raising `ROOM_NOT_FOUND (404)` on cross-org or invalid room IDs.

---

### 15. Threading Deadlock in Notifications
* **File**: `app/services/notifications.py`
* **Original Line(s)**: L31-L36
* **Buggy Code**:
```python
def notify_cancelled(booking) -> None:
    with _audit_lock:
        _write_audit("cancelled", booking)
        with _email_lock:
            _send_email("cancelled", booking)
```
* **Why it was buggy**: `notify_created` acquired locks as `_email_lock` -> `_audit_lock`, whereas `notify_cancelled` acquired them in reverse (`_audit_lock` -> `_email_lock`). This created a circular wait condition leading to deadlocks.
* **Corrected Line(s)**: L32-L36
* **Corrected Code**:
```python
def notify_cancelled(booking) -> None:
    with _email_lock:
        with _audit_lock:
            _write_audit("cancelled", booking)
            _send_email("cancelled", booking)
```
* **How it was fixed**: Standardised lock acquisition order in both functions to `_email_lock` then `_audit_lock`.

---

### 16. Shared State Concurrency Race Conditions
* **File(s)**: `app/services/ratelimit.py`, `app/services/reference.py`, `app/services/stats.py`, and `app/routers/bookings.py`
* **Why they were buggy**: Read-modify-write operations on shared in-memory dictionaries, counters, list buckets, and database overlap checks had sleeps in-between without thread lock synchronization, causing races under concurrent requests.
* **How they were fixed**: Wrapped all write paths in threading locks (room-specific locks for stats, user-specific locks for rate limits, a global counter lock for references, and a global transaction lock for booking slots and quota checks).
