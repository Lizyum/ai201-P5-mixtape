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