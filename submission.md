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

---

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

I reproduced the bug using `tests/test_feed.py::test_friends_listening_now_excludes_yesterday`. The test creates two users, establishes a friendship between them, and records a `ListeningEvent` for the friend at **June 9, 2024 at 02:00 AM UTC**. The mocked "current time" is set to **June 10, 2024 at 01:00 AM UTC** — making the event 23 hours old but on a different calendar day. Calling `get_friends_listening_now()` was expected to return an empty list, but the friend appeared in the results because 23 hours is within 24 hours.

### 2. How I found the root cause

I opened `services/feed_service.py` and looked at how the query filters `ListeningEvent` rows. At the top of the file was:

```python
RECENT_THRESHOLD = timedelta(hours=24)
```

And inside `get_friends_listening_now()`:

```python
cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD
```

This made it immediately clear: the cutoff is computed by subtracting exactly 24 hours from the current timestamp. There is no concept of a calendar day anywhere in the logic — only a fixed rolling window. Any event within the last 24 hours passes the filter, regardless of whether it happened today or yesterday.

### 3. The root cause

The module-level constant `RECENT_THRESHOLD = timedelta(hours=24)` defined a rolling window rather than a calendar boundary. Subtracting a `timedelta` from `datetime.now()` produces an absolute timestamp 24 hours in the past — it has no awareness of midnight. If the current time is 01:00 AM on June 10, the cutoff lands at 01:00 AM on June 9, which means any listen after that point is included. A friend who listened at 02:00 AM on June 9 falls inside that window even though June 9 is yesterday. The feature name "Friends Listening Now" implies same-day recency, but the query was implementing "within the last 24 hours."

### 4. My fix

I replaced the rolling-window cutoff with a calendar-day boundary by computing midnight of the current UTC date:

```python
today = datetime.now(timezone.utc).date()
cutoff = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
```

`datetime.now(timezone.utc).date()` returns only the year/month/day of today with no time component. Reconstructing a `datetime` from those three fields sets the cutoff to exactly **00:00:00 UTC today**, so only events from the current calendar day pass the `listened_at >= cutoff` filter. The `RECENT_THRESHOLD` constant was removed entirely since it was no longer used.

### 5. Side-effect check

`get_activity_feed()` is the only other function in `feed_service.py` and it intentionally has no recency filter — it returns the most recent N events regardless of date. That function was not touched and is unaffected. After the fix I confirmed the reproducing test passes and that a friend who listens earlier the same day is still correctly included.

---

## Issue 3 — Same song appears multiple times in search

### 1. How I reproduced it

I reproduced the bug using `tests/test_search.py::test_search_no_duplicates_multi_tag_song`. The `seed_songs` fixture creates a song called "Crown Heights Anthem" and inserts three rows into the `song_tags` association table — one each for the tags `rap`, `hip-hop`, and `boom bap`. Calling `search_songs("Crown Heights")` and filtering results to that title produced a list of length 3 instead of 1 — one duplicate per tag row.

### 2. How I found the root cause

I opened `services/search_service.py` and read the query in `search_songs()`:

```python
results = (
    db.session.query(Song)
    .outerjoin(song_tags, Song.id == song_tags.c.song_id)
    .filter(...)
    .all()
)
```

The `outerjoin` on `song_tags` is what caught my attention. The query starts from `Song`, joins against the `song_tags` association table, and calls `.all()` without `.distinct()`. In SQL, a `LEFT OUTER JOIN` produces one output row per matching row in the joined table. A song with three entries in `song_tags` therefore produces three joined rows, and `.all()` collects all three as separate `Song` ORM objects — even though they map to the same database record.

### 3. The root cause

`db.session.query(Song).outerjoin(song_tags, Song.id == song_tags.c.song_id)` joins each `Song` row against every matching row in `song_tags`. Because `song_tags` is a many-to-many association table, a song with N tags produces N joined rows in the result set. SQLAlchemy's ORM layer does not automatically collapse these back into a single `Song` object when `.all()` is called on a `Query` object — it returns one instance per row. The `to_dict()` call on each of those instances produces identical dicts, so the final list contains one copy of the song per tag it has.

### 4. My fix

Adding `.distinct()` before `.all()` instructs SQLAlchemy to emit a `SELECT DISTINCT` query, which deduplicates rows at the database level before the ORM constructs objects from them:

```python
results = (
    db.session.query(Song)
    .outerjoin(song_tags, Song.id == song_tags.c.song_id)
    .filter(...)
    .distinct()
    .all()
)
```

Since the `Song` primary key is included in the select, `DISTINCT` collapses the N tag-joined rows back to a single row per song before SQLAlchemy builds the result list.

### 5. Side-effect check

After applying the fix I ran the full `test_search.py` suite. The tests covering songs with zero tags (`test_search_no_duplicates_no_tag_song`) and one tag (`test_search_no_duplicates_single_tag_song`) both continued to pass — `.distinct()` is a no-op when there are no duplicate rows, so those cases are unaffected. The basic match test (`test_search_returns_matching_songs`) and the empty-result test also continued to pass.

## Issue 5 — Last song in a playlist never shows up

### 1. How I reproduced it

The bug is in `get_playlist_songs()` in `services/playlist_service.py`. To reproduce it: create a playlist, add multiple songs to it, call `get_playlist_songs()`, and compare the returned list against the songs that were added. The last song by position is always absent from the response regardless of how many songs the playlist contains.

### 2. How I found the root cause

I opened `services/playlist_service.py` and read `get_playlist_songs()`. The query itself is correct — it joins through `playlist_entries`, filters by `playlist_id`, and orders by `position`. The bug is on the return line:

```python
return [song.to_dict() for song in songs[:-1]]
```

The `[:-1]` slice on `songs` is immediately suspicious. Python's `[:-1]` means "everything except the last element," so regardless of how many songs the query returns, the final one is always dropped before the list is built.

### 3. The root cause

Python's slice notation `songs[:-1]` returns all elements of the list up to but not including the last index. Because this slice is applied to the full query result before calling `to_dict()`, the song at the highest `position` value is unconditionally excluded from every response. A single-song playlist returns an empty list; a ten-song playlist returns nine songs. There is no conditional logic — the off-by-one is always active.

### 4. My fix

I removed the `[:-1]` slice so the list comprehension iterates over the full `songs` result:

```python
return [song.to_dict() for song in songs]
```

This is the only change needed. The query already retrieves all songs in the correct order; the slice was the sole source of truncation.

### 5. Side-effect check

`get_playlist_songs()` is the only place `songs[:-1]` appeared. The other functions in `playlist_service.py` — `create_playlist()`, `get_playlist()`, and `get_user_playlists()` — do not slice their results and are unaffected. After the fix, a playlist with N songs returns exactly N songs, and the order by `position` is preserved.

## AI Usage

### 1. Codebase navigation and architecture understanding

I used AI to help build a mental model of the project before making any changes. I asked it to explain the relationships between the SQLAlchemy models, identify how requests flowed from Flask routes to service functions, and summarize which files were most relevant to each reported bug. It produced a high-level codebase map and explanations of the model relationships. I revised the generated documentation to focus only on the relationships relevant to the assigned bugs and verified each explanation against the actual implementation before using it to guide my debugging.

### 2. Root cause analysis and debugging

I used AI as a debugging assistant to reason through failing tests and trace each bug to its underlying cause. Rather than asking it to generate fixes directly, I discussed the observed test failures, explored possible explanations, and validated those hypotheses by inspecting the service implementations myself. After identifying the root cause, I implemented the fixes manually and reran the project's test suite to confirm the intended behavior while checking that no existing functionality had regressed.