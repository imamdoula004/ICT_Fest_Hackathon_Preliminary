# CoWork REST API — Solution Walkthrough

Welcome to the solution walkthrough for the CoWork Multi-Tenant Coworking Space Booking API. This document demonstrates our concurrency model, timezone normalization pipeline, and highlights the **17 bugs** we successfully identified and fixed.

---

## ⚡ Concurrency & Lock Architecture

We solved critical concurrency bugs (such as deadlocks, lost updates, and race conditions) by designing a granular lock isolation architecture.

### Deadlock Elimination (Lock Acquisition Order)
The original service crashed under concurrent load because `notify_created` acquired locks in the order `Email -> Audit`, while `notify_cancelled` acquired them in reverse (`Audit -> Email`). We standardized the lock acquisition order across the entire application to prevent deadlocks:

![Concurrency Walkthrough](docs/concurrency_walkthrough.svg)

---

## 🌐 Datetime Offset & Storage Pipeline

Per Business Rule 1, all datetimes carrying an offset must be converted to UTC before storage or comparison, and naive datetimes are treated as UTC. 

Our pipeline converts offsets dynamically before database persistence, ensuring the database holds only clean, naive UTC timestamps:

![Timezone Normalization](docs/timezone_normalization.svg)

---

## 🛠️ The 17-Bug Fix Catalog

Here is the complete catalog of bugs fixed, including line numbers and original vs. corrected code.

### 1. Datetime Parsing Offset Loss
* **File**: `app/timeutils.py` (L12-L13)
* **Bug**: Stripped timezone offset using `dt.replace(tzinfo=None)` directly without converting hours, distorting the database record.
* **Fix**: Converted `dt` to UTC first using `dt.astimezone(timezone.utc)` before stripping the timezone info.

---

### 2. Token Lifetime Duration
* **File**: `app/auth.py` (L50)
* **Bug**: Multiplied minutes by 60 when passing it to `minutes` parameter of `timedelta`, resulting in 15 hours expiration.
* **Fix**: Changed duration parameter to `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)`.

---

### 3. Revoked Access Token Verification
* **File**: `app/auth.py` (L97)
* **Bug**: Checked user ID `sub` against `_revoked_tokens` instead of checking the token ID `jti`, leaving logged-out tokens valid.
* **Fix**: Changed the validation check to check the token's `jti` instead of `sub` against the revoked token set.

---

### 4. Duplicate Username Registration Response
* **File**: `app/routers/auth.py` (L37-L43)
* **Bug**: Returned duplicate usernames as `201 Created` instead of raising a `409 USERNAME_TAKEN` conflict.
* **Fix**: Enforced raising `AppError(409, "USERNAME_TAKEN", ...)` on duplicate user registration.

---

### 5. Multi-Thread Registration Race Condition
* **File**: `app/routers/auth.py` (L26-L30, L52-L53)
* **Bug**: Simultaneous registrations for the same user could bypass the python checks, resulting in a database unique constraint crash (500 error).
* **Fix**: Caught `IntegrityError` from SQLAlchemy on db commits, rolled back the failed session, and raised `USERNAME_TAKEN (409)`.

---

### 6. Refresh Token Reuse
* **File**: `app/routers/auth.py` (L81-L93)
* **Bug**: Refresh tokens were not validated for single-use, letting them be reused infinitely.
* **Fix**: Added a check using `is_token_revoked` and called `revoke_token(jti)` immediately on usage inside a thread lock.

---

### 7. Past Start Time Grace Window
* **File**: `app/routers/bookings.py` (L86-L87)
* **Bug**: Allowed booking slots starting in the past (up to 5 minutes ago).
* **Fix**: Changed the boundary check to `start <= now` to enforce strictly future bookings.

---

### 8. Minimum Booking Duration Validation
* **File**: `app/routers/bookings.py` (L93-L94)
* **Bug**: The code did not enforce a minimum duration of 1 hour, allowing 0 or negative hours.
* **Fix**: Raised `INVALID_BOOKING_WINDOW` (400) if `duration_hours < MIN_DURATION_HOURS`.

---

### 9. Booking Creation Cache Invalidation
* **File**: `app/routers/bookings.py` (L116-L121)
* **Bug**: Creating a new booking did not invalidate the usage report cache, leading to stale usage reports.
* **Fix**: Added `cache.invalidate_report(user.org_id)` after booking creation.

---

### 10. Booking Pagination Offset
* **File**: `app/routers/bookings.py` (L137-L141)
* **Bug**: Pagination offset was calculated as `page * limit` which skips the first page.
* **Fix**: Corrected the formula to `(page - 1) * limit`.

---

### 11. Booking Pagination Limit
* **File**: `app/routers/bookings.py` (L137-L141)
* **Bug**: The endpoint had limit hardcoded to `.limit(10)` instead of using the user-supplied `limit` parameter.
* **Fix**: Changed limit to `.limit(limit)`.

---

### 12. Booking Pagination Sorting
* **File**: `app/routers/bookings.py` (L137-L141)
* **Bug**: Ordered items descending instead of ascending by start time.
* **Fix**: Sorted ascending by `start_time` and secondary `id`.

---

### 13. Single Booking Start Time Override
* **File**: `app/routers/bookings.py` (L166)
* **Bug**: GET `/bookings/{id}` overrode `start_time` in response with the booking's `created_at` timestamp.
* **Fix**: Removed the overriding statement.

---

### 14. Cancellation Notice Windows & Refund Rates
* **File**: `app/routers/bookings.py` (L199-L206)
* **Bug**: Notice exactly equal to 48 hours returned 50% instead of 100%, and notice less than 24 hours returned 50% instead of 0%.
* **Fix**: Corrected the notice window boundaries and set the `else` refund rate to 0%.

---

### 15. Half-Cent Rounding Up in Refunds
* **File**: `app/routers/bookings.py` (L208) and `app/services/refunds.py` (L15-L17)
* **Bug**: Float conversions or Banker's rounding rounded half-cents down (e.g. `52.5` cents became `52` cents) instead of rounding half-cents up to the next integer cent.
* **Fix**: Used exact integer arithmetic `(price + 1) // 2` to guarantee half-cents round up.

---

### 16. Admin Export Multi-Tenancy Security
* **File**: `app/routers/admin.py` (L65-L73)
* **Bug**: The endpoint did not verify whether the passed `room_id` belonged to the admin's organization, allowing cross-org exports.
* **Fix**: Validated room organization ownership before proceeding, raising `ROOM_NOT_FOUND (404)` on cross-org or invalid room IDs.

---

### 17. Concurrency Race Conditions (Counters, Stats, Rate limits)
* **File**: `app/services/ratelimit.py`, `app/services/reference.py`, `app/services/stats.py`, and `app/routers/bookings.py`
* **Bug**: Shared state was updated concurrently with sleeps in-between, causing races and lost updates.
* **Fix**: Wrapped writes in thread-safe locks (room locks, user locks, reference locks, and transaction locks).

---

## 🧪 Testing & Verification

We implemented a robust test suite in `tests/test_edge_cases.py` to assert correct behavior.

All 11 tests passed successfully:
```
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
collected 11 items

tests/test_edge_cases.py ..........                                      [ 90%]
tests/test_smoke.py .                                                    [100%]

======================== 11 passed, 1 warning in 32.46s ========================
```
