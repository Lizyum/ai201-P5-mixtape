"""
tests/test_feed.py — Mixtape

Tests for the Friends Listening Now and activity feed logic.
"""

import pytest
from datetime import datetime as real_datetime, timezone
from unittest.mock import patch
from app import create_app, db
from models import User, Song, ListeningEvent
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def test_friends_listening_now_excludes_yesterday(app):
    """
    A friend who listened yesterday should not appear in Friends Listening Now,
    even if the event is within 24 hours of the current time.

    Bug: get_friends_listening_now uses a rolling 24-hour window, so a friend
    who listened at 02:00 on June 9 shows up when "now" is 01:00 on June 10
    (only 23 hours ago), even though the listen happened on a different calendar day.
    """
    with app.app_context():
        # Create users
        user = User(username="alice", email="alice@example.com")
        friend = User(username="bob", email="bob@example.com")
        db.session.add_all([user, friend])
        db.session.commit()

        # Create friendship
        user.friends.append(friend)
        db.session.commit()

        # Create song
        song = Song(title="Yesterday's Jam", artist="Test Artist", shared_by=user.id)
        db.session.add(song)
        db.session.commit()

        # Create a ListeningEvent that is yesterday but within 24 hours:
        # "now" = June 10 at 01:00 AM, listened_at = June 9 at 02:00 AM (23 hours ago, but yesterday)
        fake_now = real_datetime(2024, 6, 10, 1, 0, 0, tzinfo=timezone.utc)
        listened_at = real_datetime(2024, 6, 9, 2, 0, 0, tzinfo=timezone.utc)

        event = ListeningEvent(user_id=friend.id, song_id=song.id, listened_at=listened_at)
        db.session.add(event)
        db.session.commit()

        with patch("services.feed_service.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            # Pass constructor calls through to the real datetime so SQLite
            # receives actual datetime objects instead of MagicMocks
            mock_dt.side_effect = real_datetime
            result = get_friends_listening_now(user.id)

        assert result == [], (
            "Expected no results: friend listened yesterday, not today, "
            "so they should not appear in Friends Listening Now"
        )
