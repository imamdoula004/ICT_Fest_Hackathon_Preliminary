# Bug Report: CoWork API

This report documents the bugs discovered in the CoWork REST API codebase and how they were fixed.

---

### 1. Datetime Parsing Offset Loss
* **Location**: [app/timeutils.py](file:///app/timeutils.py)
* **Issue**: The `parse_input_datetime` helper stripped timezone offsets using `dt.replace(tzinfo=None)` directly, without converting the time to UTC first. This resulted in wrong timestamps stored in the database (e.g., `2026-07-09T18:00:00+02:00` was stored as `18:00:00` instead of `16:00:00` UTC).
* **Fix**: Normalised inputs with a timezone offset to UTC using `dt.astimezone(timezone.utc)` before stripping the timezone info.

---

### 2. Token Lifetime Calculation
* **Location**: [app/auth.py](file:///app/auth.py)
* **Issue**: Access token lifetime was set to `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)`. Since `ACCESS_TOKEN_EXPIRE_MINUTES` is defined as `15`, this evaluated to 15 hours instead of 15 minutes (900 seconds), violating Business Rule 8.
* **Fix**: Adjusted the lifetime to `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)`.

---

### 3. Revoked Access Token Verification
* **Location**: [app/auth.py](file:///app/auth.py)
* **Issue**: In `get_token_payload`, the check for revoked tokens used `payload.get("sub")` (user ID) instead of `payload.get("jti")` (token ID), meaning logged-out tokens were not correctly validated.
* **Fix**: Changed the lookup to check `payload.get("jti")` against `_revoked_tokens`. Exposed `is_token_revoked` and `revoke_token` functions to allow consistent checking.

---

### 4. Duplicate Username Registration Response
* **Location**: [app/routers/auth.py](file:///app/routers/auth.py)
* **Issue**: If a user tried to register with an existing username in their organization, the `/register` endpoint returned the user details with a `201 Created` status instead of raising a `409 Conflict` (code `USERNAME_TAKEN`).
* **Fix**: Added an check to raise `AppError(409, "USERNAME_TAKEN", "...")` if the user exists.

---

### 5. Multi-Thread Registration Race Condition
* **Location**: [app/routers/auth.py](file:///app/routers/auth.py)
* **Issue**: Under concurrent user registration, two threads could check `existing` simultaneously, see it doesn't exist, and attempt to write. This causes SQLite constraint violation and a 500 error.
* **Fix**: Wrapped organization and user creation commits in try-except blocks catching `IntegrityError`, rolling back and raising `USERNAME_TAKEN (409)` on conflict.

---

### 6. Refresh Token Reuse
* **Location**: [app/routers/auth.py](file:///app/routers/auth.py)
* **Issue**: Refresh tokens were not validated for single-use, allowing infinite reuse of the same refresh token.
* **Fix**: Checked if the presented refresh token's `jti` was already revoked, and revoked it immediately upon use inside a thread lock (`_refresh_lock`).

---

### 7. Past Booking Grace Period
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: Bookings could start up to 5 minutes in the past due to `start <= now - timedelta(seconds=300)`.
* **Fix**: Changed condition to `start <= now` to enforce strictly future bookings with no grace window.

---

### 8. Minimum Booking Duration Validation
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: The check for booking duration did not enforce a minimum length of 1 hour, allowing 0 or negative hour durations to pass.
* **Fix**: Raised `INVALID_BOOKING_WINDOW` (400) if `duration_hours < MIN_DURATION_HOURS`.

---

### 9. Booking Creation Cache Invalidation
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: When a new booking was created, the usage report cache was not invalidated (`cache.invalidate_report(user.org_id)` was missing), causing the admin usage report to return stale data.
* **Fix**: Added `cache.invalidate_report(user.org_id)` to `create_booking`.

---

### 10. Booking Pagination Offset
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: Pagination offset was calculated as `page * limit`, skipping the first page's items when `page = 1`.
* **Fix**: Corrected the formula to `(page - 1) * limit`.

---

### 11. Booking Pagination Limit
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: The endpoint had limit hardcoded to `.limit(10)` instead of using the user-supplied `limit` query parameter.
* **Fix**: Changed limit to `.limit(limit)`.

---

### 12. Booking Pagination Sorting
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: The endpoint ordered items by `start_time` descending (`Booking.start_time.desc()`) instead of ascending.
* **Fix**: Changed ordering to `.order_by(Booking.start_time.asc(), Booking.id.asc())`.

---

### 13. Single Booking Start Time Override
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: The GET `/bookings/{id}` endpoint overrode `response["start_time"]` with the booking's `created_at` timestamp.
* **Fix**: Removed the overriding statement to preserve the actual slot start time.

---

### 14. Cancellation Notice Windows & Refund Rates
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: The notice calculation was incorrect:
  1. Notice exactly equal to 48 hours returned 50% instead of 100%.
  2. Notice less than 24 hours fell into the `else` branch which gave 50% instead of 0%.
* **Fix**: Corrected notice checking:
  - `notice >= timedelta(hours=48)` -> 100% refund
  - `notice >= timedelta(hours=24)` -> 50% refund
  - Else -> 0% refund

---

### 15. Half-Cent Rounding Up in Refunds
* **Location**: [app/routers/bookings.py](file:///app/routers/bookings.py) and [app/services/refunds.py](file:///app/services/refunds.py)
* **Issue**: The refund calculation cast floats to integers (in `refunds.py`) or used `round()` (in `bookings.py`), which truncates or uses Banker's rounding. This rounded half-cents down (e.g. `52.5` to `52`).
* **Fix**: Replaced floating-point division and truncation with exact integer arithmetic `(price + 1) // 2` to guarantee half-cents round up to the next integer cent (e.g. `1005` price -> `503` refund).

---

### 16. Admin Export Multi-Tenancy Security
* **Location**: [app/routers/admin.py](file:///app/routers/admin.py)
* **Issue**: The `/admin/export` endpoint allowed admins to pass a `room_id` belonging to a different organization and export its bookings, violating multi-tenancy rules.
* **Fix**: Added validation to verify `room_id` exists and belongs to the admin's organization; otherwise, raising `ROOM_NOT_FOUND (404)`.

---

### 17. Threading Deadlock in Notifications
* **Location**: [app/services/notifications.py](file:///app/services/notifications.py)
* **Issue**: `notify_created` acquired locks in order `_email_lock` -> `_audit_lock`, while `notify_cancelled` acquired them as `_audit_lock` -> `_email_lock`. This created a cyclic dependency causing deadlocks.
* **Fix**: Standardized the lock acquisition order in both functions to `_email_lock` then `_audit_lock`.

---

### 18. Shared State Concurrency Races
* **Location**: [app/services/ratelimit.py](file:///app/services/ratelimit.py), [app/services/reference.py](file:///app/services/reference.py), [app/services/stats.py](file:///app/services/stats.py), and [app/routers/bookings.py](file:///app/routers/bookings.py)
* **Issue**: Monotonic counters, user rate limit buckets, room statistics, and booking overlaps were read and updated with intermediate sleeps without thread synchronization.
* **Fix**: Protected writes and validations using threading locks (global locks for booking slots and reference codes, user-specific locks for rate limits, and room-specific locks for stats).
