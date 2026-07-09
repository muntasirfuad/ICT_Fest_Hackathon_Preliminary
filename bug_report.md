# Bug Fixes Log

A rundown of the bugs we found and fixed, organized by who worked on what.

---

## Tonoy's Fixes

### 1. Access token expiry was way too long
**File:** `auth.py` — line 50

The expiry calculation was multiplying minutes by 60, which turned "expire in X minutes" into "expire in X hours" (60x longer than intended).

```python
# Before
timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)

# After
timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
```

### 2. Duplicate usernames were silently allowed
**File:** `routers/auth.py` — lines 37-43

If a username already existed in an org, the code just returned the existing user's info instead of blocking the signup. Now it correctly raises a conflict error.

```python
# Before
if existing is not None:
    return {
        "user_id": existing.id,
        "org_id": org.id,
        "username": existing.username,
        "role": existing.role,
    }

# After
if existing is not None:
    raise AppError(409, "USERNAME_TAKEN", "Username already taken in this organization")
```

### 3. Race condition generating reference codes
**File:** `services/reference.py` — `next_reference_code()`, lines 16-19

The counter was read, then there was a 120ms pause (`_format_pause()`), and only after that was it incremented. Two concurrent requests could read the same value in that window and get duplicate reference codes.

**Fix:** move the increment right after the read, before the pause — and/or wrap the whole read-modify-write in a `threading.Lock`.

### 4 & 5. Missing cache invalidation on booking creation
**File:** `bookings.py` — lines 216-218 and 120-122

Two separate spots that record a new booking were missing the `cache.invalidate_report(user.org_id)` call, meaning org-level reports could show stale data after a booking was created. Added the missing line in both places.

```python
stats.record_create(room.id, price_cents)
cache.invalidate_availability(room.id, start.date().isoformat())
cache.invalidate_report(user.org_id)   # ← added
notifications.notify_created(booking)
```

### 6. Cross-tenant data leak in admin export
**File:** `admin.py` (via `services/export.py:fetch_bookings_raw()`)

The export endpoint's raw fetch function was missing an `org_id` filter, which meant `GET /admin/export?room_id=<any>&include_all=true` could pull booking data belonging to a different organization entirely.

### 7. Validation ran after the cache check
**File:** `admin.py` — `routers/admin.py:usage_report()`

The cache lookup happened before the date range validation. That meant an invalid `from`/`to` window could sneak past the `INVALID_BOOKING_WINDOW` check whenever there was a cache hit. Validation now runs first.

### 8. Audit log and email locks unified for cancellations
**File:** `app/services/notifications.py` — lines 31-35

```python
def notify_cancelled(booking) -> None:
    with _audit_lock:
        _write_audit("cancelled", booking)
    with _email_lock:
        _send_email("cancelled", booking)
```

### 9. Timezone stripped incorrectly
**File:** `app/timeulits.py` — line 13

```python
dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
```

### 10. Rate limiter could be bypassed entirely
**File:** `services/ratelimit.py`

Same class of bug as #3 — the bucket dict was read, slept on (`_settle_pause()`), then written, with no locking. Concurrent requests could all read the same "not yet limited" state and blow straight through the rate limit.

**Fix:** wrapped the check in a `threading.Lock` and removed the pointless sleep that was widening the race window.

### 11. Stats corruption under concurrent cancels/creates
**File:** `services/stats.py`

`record_cancel` wasn't acquiring `_lock` at all, so it raced unprotected against itself *and* against `record_create` (which does hold the lock) — causing lost updates and wrong counts/revenue under load. On top of that, revenue was never floor-clamped like count was, so it could go negative.

### 12. Same cross-tenant leak, fixed at the source
**File:** `app/services/export.py` — lines 22-29, 48-50

This is the actual fix for bug #6 — added the `org_id` filter directly into the join/filter chain.

```python
def fetch_bookings_raw(db: Session, org_id: int, room_id: int) -> list[Booking]:
    """Load every booking for a single room within the given org, ordered by id."""
    return (
        db.query(Booking)
        .join(Room)
        .filter(Booking.room_id == room_id, Room.org_id == org_id)
        .order_by(Booking.id.asc())
        .all()
    )
```

And the calling code (lines 48-50) now looks up the room scoped to `org_id` before fetching, raising `ROOM_NOT_FOUND` if it doesn't match:

```python
if include_all:
    if room_id is not None:
        room = db.query(Room).filter(Room.id == room_id, Room.org_id == org_id).first()
        if room is None:
            raise AppError(404, "ROOM_NOT_FOUND", "Room not found")
        rows = fetch_bookings_raw(db, org_id, room_id)
    else:
        rows = _fetch_scoped(db, org_id, None, None)
```

### 13. Revoked access token check
**File:** `app/auth.py` — line 97

```python
if payload.get("jti") in _revoked_tokens:
```

### 14. Refresh tokens could be reused after refresh
**File:** `app/routers/auth.py` — `refresh()` function, lines 81-93

Refresh tokens weren't being invalidated after use, so the same refresh token could be replayed indefinitely. Added a revocation set and a `revoke_refresh_token()` helper, and the refresh endpoint now checks against it and revokes the token once it's been used.

```python
# app/auth.py — added
def revoke_refresh_token(payload: dict) -> None:
    _revoked_refresh_tokens.add(payload["jti"])

_revoked_refresh_tokens: set[str] = set()  # sits next to _revoked_tokens

# app/routers/auth.py — refresh() correction
@router.post("/refresh")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    data = decode_token(payload.refresh_token)
    if data.get("type") != "refresh":
        raise AppError(401, "UNAUTHORIZED", "Wrong token type")
    if data.get("jti") in _revoked_refresh_tokens:
        raise AppError(401, "UNAUTHORIZED", "Refresh token already used")
    user = db.query(User).filter(User.id == int(data["sub"])).first()
    if user is None:
        raise AppError(401, "UNAUTHORIZED", "Unknown user")
    revoke_refresh_token(data)
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
        "token_type": "bearer",
    }
```

---

## Mustakim's Fixes

**File:** `booking.py` (all of the following)

### a. Grace period removed from cancellation cutoff
**Line 86**

```python
# Before
if start <= now - timedelta(seconds=300):

# After
if start <= now:
```

### b. Dead/unused line removed
**Line 166** — deleted entirely.

### c. Overlap check was letting back-to-back bookings collide
**Line 50**

Using `<=` on both ends meant a booking ending exactly when another started counted as a conflict (or let two bookings share a boundary incorrectly, depending on direction). Switched to strict inequality.

```python
# Before
b.start_time <= end and start <= b.end_time

# After
b.start_time < end and start < b.end_time
```

### d. Duration validation was missing a zero/negative check
**Lines 89-94**

Nothing stopped a booking with an end time before (or equal to) the start time — it would just produce a negative or zero duration and slip through. Added an explicit check for that case.

```python
duration_hours = (end - start).total_seconds() / 3600
if duration_hours <= 0:
    raise AppError(400, "INVALID_BOOKING_WINDOW", "end_time must be after start_time")
if duration_hours != int(duration_hours):
    raise AppError(400, "INVALID_BOOKING_WINDOW", "duration must be a whole number of hours")
duration_hours = int(duration_hours)
if duration_hours > MAX_DURATION_HOURS:
    raise AppError(400, "INVALID_BOOKING_WINDOW", "duration out of range")
if duration_hours < MIN_DURATION_HOURS:
    raise AppError(400, "INVALID_BOOKING_WINDOW", "duration out of range")
```

### e. Pagination and sort order were both wrong
**Lines 140-143**

Results were sorted newest-first when they should've been chronological, the page offset math was off by one page, and the page size was hardcoded to 10 instead of respecting `limit`.

```python
# Before
items = (
    base.order_by(Booking.start_time.desc(), Booking.id.asc())
    .offset(page * limit)
    .limit(10)
    .all()
)

# After
items = (
    base.order_by(Booking.start_time.asc(), Booking.id.asc())
    .offset((page - 1) * limit)
    .limit(limit)
    .all()
)
```

### f. Refund policy had a boundary bug and a missing tier
**Lines 201-210**

Cancelling with *exactly* 48 hours notice fell through to the wrong tier because the check used `>` instead of `>=`. Also, there was no partial-refund tier for 24-48 hours notice — it jumped straight from 100% to 0%. Added the 50% tier.

```python
now = datetime.utcnow()
notice = booking.start_time - now
notice_hours = int(notice.total_seconds() // 3600)
if notice_hours >= 48:
    refund_percent = 100
elif notice >= timedelta(hours=24):
    refund_percent = 50
else:
    refund_percent = 0
```

### g. Booking creation and cancellation had no concurrency protection
**Line 104** (creation) and **line 214** (cancellation)

Nothing stopped two concurrent requests from double-booking the same room or double-cancelling the same booking. Added per-room and per-booking locks.

```python
with _get_room_lock(room.id):
    if _has_conflict(db, room.id, start, end):
        raise AppError(409, "ROOM_CONFLICT", "Room already booked for this interval")

    _check_quota(db, user.id, now, start)
```

New locking infrastructure added near the top of the file:

```python
import threading

_room_locks: dict[int, threading.Lock] = {}
_booking_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()

def _get_room_lock(room_id: int) -> threading.Lock:
    with _locks_guard:
        return _room_locks.setdefault(room_id, threading.Lock())

def _get_booking_lock(booking_id: int) -> threading.Lock:
    with _locks_guard:
        return _booking_locks.setdefault(booking_id, threading.Lock())
```

And the cancellation path:

```python
with _get_booking_lock(booking.id):
    if booking.status == "cancelled":
        raise AppError(409, "ALREADY_CANCELLED", "Booking already cancelled")

    now = datetime.utcnow()
    ...
```

### h. Refund math pulled into its own function
**Line 16** (new function) and **line 227**

The refund calculation was inlined at the point of use. Pulled it out into a reusable `calc_refund_cents()` function so the rounding logic lives in one place.

```python
# Before
refund_amount_cents = round(booking.price_cents * (refund_percent / 100.0))

# After
refund_amount_cents = calc_refund_cents(booking.price_cents, refund_percent)
```

`calc_refund_cents` was added as a new helper function used by the refund flow.

---

## Quick Reference Table

| # | Owner | File | Line(s) | Issue |
|---|-------|------|---------|-------|
| 1 | Tonoy | `auth.py` | 50 | Access token expiry 60x too long |
| 2 | Tonoy | `routers/auth.py` | 37-43 | Duplicate usernames allowed |
| 3 | Tonoy | `services/reference.py` | 16-19 | Race condition generating reference codes |
| 4 | Tonoy | `bookings.py` | 216-218 | Missing report cache invalidation |
| 5 | Tonoy | `bookings.py` | 120-122 | Missing report cache invalidation |
| 6 | Tonoy | `admin.py` / `services/export.py` | — | Cross-tenant data leak in export |
| 7 | Tonoy | `admin.py` (`routers/admin.py`) | — | Validation ran after cache lookup |
| 8 | Tonoy | `app/services/notifications.py` | 31-35 | Audit/email lock handling on cancel |
| 9 | Tonoy | `app/timeulits.py` | 13 | Timezone stripped incorrectly |
| 10 | Tonoy | `services/ratelimit.py` | — | Rate limiter bypassable via race condition |
| 11 | Tonoy | `services/stats.py` | — | Unlocked `record_cancel`, negative revenue |
| 12 | Tonoy | `app/services/export.py` | 22-29, 48-50 | Fix for cross-tenant leak (org_id filter) |
| 13 | Tonoy | `app/auth.py` | 97 | Revoked access token check |
| 14 | Tonoy | `app/routers/auth.py` | 81-93 | Refresh tokens reusable after use |
| a | Mustakim | `booking.py` | 86 | Removed 300s cancellation grace period |
| b | Mustakim | `booking.py` | 166 | Deleted dead line |
| c | Mustakim | `booking.py` | 50 | Overlap check boundary fix |
| d | Mustakim | `booking.py` | 89-94 | Added zero/negative duration check |
| e | Mustakim | `booking.py` | 140-143 | Fixed sort order, pagination offset, limit |
| f | Mustakim | `booking.py` | 201-210 | Fixed refund boundary, added 50% tier |
| g | Mustakim | `booking.py` | 104, 214 | Added room/booking locks |
| h | Mustakim | `booking.py` | 16, 227 | Extracted `calc_refund_cents()` |
