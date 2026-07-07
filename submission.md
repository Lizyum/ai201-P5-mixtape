# Codebase Map

Architecture overview, data flow, and bug surface guide.

---

# Models

`models.py` defines seven SQLAlchemy models and three association tables.

### Core Relationships

```
User
 ├── shared_songs → Song
 ├── playlists → Playlist
 ├── ratings → Rating
 ├── listening_events → ListeningEvent
 ├── notifications → Notification
 └── friends ↔ User (friendships)

Song
 ├── shared_by → User
 ├── ratings → Rating
 ├── listening_events → ListeningEvent
 ├── tags ↔ Tag (song_tags)
 └── playlists ↔ Playlist (playlist_entries)
```

### Association Tables

| Table | Purpose |
|--------|---------|
| `friendships` | User ↔ User friendships (many-to-many) |
| `song_tags` | Song ↔ Tag relationship (many-to-many) |
| `playlist_entries` | Playlist ↔ Song relationship. Stores `position`, `added_by`, and `added_at` so playlist order is explicit. |

### Bug-Relevant Models

- **User**
  - `listening_streak` and `last_listened_at` are the only fields that track streaks. They are updated directly by `streak_service.py`.
  - `friends` is a self-referential many-to-many relationship used by the Friends Listening Now feed.

- **ListeningEvent**
  - Records every song listen (`user_id`, `song_id`, `listened_at`).
  - Creating a `ListeningEvent` triggers streak updates.

- **Song**
  - Connected to `Tag` through the `song_tags` association table.
  - This many-to-many relationship is the source of duplicate search results when joined without `.distinct()`.

---

# App Structure

`app.py` uses the Flask application factory pattern.

`db = SQLAlchemy()` is created once and initialized inside `create_app()`.

Four blueprints are registered:

```
/songs
/playlists
/users
/feed
```

Every route follows the same pattern:

```
Request
    ↓
Blueprint Route
    ↓
Service Function
    ↓
Database
    ↓
JSON Response
```

Routes remain intentionally thin.

Business logic lives almost entirely inside `services/`.

When debugging, service files are almost always the correct place to investigate.

---

# Data Flow — Listening Streak

Complete request lifecycle when updating a user's streak.

```
POST /songs/<song_id>/listen
    ↓
routes/songs.py

Extract user_id from request body

    ↓
services/streak_service.py

Load User

db.session.get(User, user_id)

    ↓

Create ListeningEvent

ListeningEvent(
    user_id,
    song_id,
    listened_at=now
)

    ↓

update_listening_streak(user, now)

    ↓

last_listened_at is None?
    → streak = 1

else

days_since_last = today - last_listened

0 days
    → no change

1 day
    → increment streak

otherwise
    → reset streak

    ↓

Update user.last_listened_at

    ↓

db.session.commit()
```

A single transaction persists:

- the new `ListeningEvent`
- the updated `User`

---

# Bug Reproduction Plan

## Issue 1 — Listening streak resets on Sunday

**Bug:** A user who listens on Saturday and then Sunday should have their streak increment, but it resets to 1.

**How to reproduce:**

1. Create a test user.
2. Set their first listen to **Saturday, June 15, 2024**.
3. Call `update_listening_streak(user, saturday)`.
4. Verify the streak is `1`.
5. Set their next listen to **Sunday, June 16, 2024**.
6. Call `update_listening_streak(user, sunday)`.
7. **Expected:** `listening_streak == 2`
8. **Actual:** `listening_streak == 1`

**Test used:**

`tests/test_streaks.py::test_streak_increments_on_sunday`

**Run with:**

```bash
pytest tests/test_streaks.py::test_streak_increments_on_sunday
```

**Trigger condition:** A consecutive listen where the second day is Sunday.

---

## Issue 2 — Friends Listening Now shows people from yesterday

**Bug:** The feed uses a rolling 24-hour threshold instead of a calendar-day or "currently listening" window.

**How to reproduce:**

1. Create two users.
2. Create a friendship between them.
3. Create a song.
4. Create a `ListeningEvent` for the friend from **yesterday**, but less than 24 hours ago.
   - Example:
     - Current time: Today, 10:00 PM
     - Listening event: Yesterday, 11:00 PM
5. Call the Friends Listening Now service.
6. **Expected:** Yesterday's listening event is not returned.
7. **Actual:** The event is returned because it falls within the last 24 hours.

**Test Used:**

`tests/test_feed.py::test_friends_listening_now_excludes_yesterday`

**Trigger condition:** A listening event occurred on the previous calendar day but within the last 24 hours.

---

## Issue 3 — Same song appears multiple times in search

**Bug:** Songs with multiple tags appear multiple times because the search query joins against `song_tags` without removing duplicate rows.

**How to reproduce:**

1. Create a user.
2. Create a song.
3. Associate the song with multiple tags.
4. Search for the song by tag.
5. **Expected:** One search result.
6. **Actual:** One result per matching joined tag row.

**Test used:**

`tests/test_search.py::test_search_no_duplicates_multi_tag_song_by_tag`

**Run with:**

```bash
pytest tests/test_search.py::test_search_no_duplicates_multi_tag_song_by_tag
```

**Trigger condition:** The searched song has multiple entries in the `song_tags` association table, and the search query joins through tags without using `.distinct()`.

---

# Bug Surfaces

## Bug 1 — Listening streak resets on Sunday

**Location**

```
services/streak_service.py
```

Current logic:

```python
days_since_last == 1 and today.weekday() != 6
```

Because Sunday has weekday `6`, Saturday → Sunday falls into the reset branch.

Expected:

```
Saturday
↓

Sunday

↓

streak += 1
```

Current behavior:

```
Saturday
↓

Sunday

↓

streak = 1
```

---

## Bug 2 — Friends Listening Now includes yesterday

**Location**

```
services/feed_service.py
```

Current implementation:

```python
RECENT_THRESHOLD = timedelta(hours=24)
```

The feature says "Listening Now," but the query actually means "within the last 24 hours."

Example:

```
Yesterday 11:30 PM
↓

Today 10:45 PM

= still returned
```

---

## Bug 3 — Duplicate search results

**Location**

```
services/search_service.py
```

Current query performs:

```
Song
LEFT OUTER JOIN
song_tags
```

A song with multiple tags produces multiple rows.

Example:

```
Song A

Pop
Rock
Workout
```

Result:

```
Song A
Song A
Song A
```

The query should call:

```python
.distinct()
```

before `.all()` so each song appears only once.

## Bug 4 — No notification when a friend rates your song

**Location:** `services/notification_service.py`

`rate_song()` creates and commits the `Rating`, but never calls `create_notification()`.

Compare this with `add_to_playlist()`, which notifies the song’s sharer when their song is added to a playlist.

**Expected behavior:**

```text
Friend rates your shared song
    ↓
Rating is created
    ↓
Notification is created for song owner
```

**Current behavior:**

```text
Friend rates your shared song
    ↓
Rating is created
    ↓
No notification is sent
```

---

## Bug 5 — Last song in a playlist never shows up

**Location:** `services/playlist_service.py`

Current logic:

```python
return [song.to_dict() for song in songs[:-1]]
```

The `[:-1]` slice silently drops the final song from every playlist response.

Expected fix:

```python
return [song.to_dict() for song in songs]
```


# Bug Root Cause Analysis

## Issue 1 — Listening streak resets on Sunday

### 1. How I reproduced it

I reproduced the bug using `tests/test_streaks.py::test_streak_increments_on_sunday`. The test creates a new user, records a listen on Saturday, June 15, 2024, followed by another listen on Sunday, June 16, 2024. The expected streak was `2`, but the test failed because the streak reset to `1`.

### 2. How I found the root cause

Starting from the failing test, I followed the call to `update_listening_streak()` in `services/streak_service.py`. The consecutive-day logic contained the condition:

```python
elif days_since_last == 1 and today.weekday() != 6:
```

Seeing that `datetime.weekday()` returns `6` for Sunday made it clear that Sunday was being intentionally excluded from the increment branch.

### 3. The root cause

The streak increment logic only executed when `days_since_last == 1` **and** the current day was **not** Sunday. Because Sunday (`weekday() == 6`) failed this condition, a valid Saturday-to-Sunday consecutive listen fell through to the reset branch, causing the streak to restart at `1` instead of incrementing.

### 4. My fix

I removed the `today.weekday() != 6` condition so that any consecutive-day listen increments the streak regardless of the day of the week.

### 5. Side-effect check

After applying the fix, I reran the streak test suite. In addition to confirming that the Sunday regression test now passed, I verified that the existing behaviors remained unchanged: new users start with a streak of `1`, multiple listens on the same day do not increase the streak, consecutive weekdays still increment correctly, and skipping a day still resets the streak.

---

## Issue 2 — Friends Listening Now shows people from yesterday

### 1. How I reproduced it
...

### 2. How I found the root cause
...

### 3. The root cause
...

### 4. My fix
...

### 5. Side-effect check
...

---

## Issue 3 — Same song appears multiple times in search

### 1. How I reproduced it
...

### 2. How I found the root cause
...

### 3. The root cause
...

### 4. My fix
...

### 5. Side-effect check
...