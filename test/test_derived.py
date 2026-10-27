"""Which images were squished, and from what, held in memory (#888)."""

import logging

import pytest

from harmonist import activity_store, derived


@pytest.fixture(autouse=True)
def store(tmp_path):
    activity_store.init(tmp_path / "activity.db")
    derived.reset()
    yield
    derived.reset()


def test_a_recorded_squish_answers_as_its_original():
    assert derived.originals() == {}  # read before the squish, and kept
    derived.record("squished", original="original")

    assert derived.originals() == {"squished": "original"}
    # Durable, not just remembered: a restart reads it back.
    derived.reset()
    assert derived.originals() == {"squished": "original"}


def test_what_the_store_holds_is_read_once(monkeypatch):
    activity_store.record_derived_image("squished", original="original")
    reads = []
    real = activity_store.derived_images
    monkeypatch.setattr(activity_store, "derived_images", lambda: reads.append(1) or real())

    derived.originals()
    derived.originals()

    assert len(reads) == 1


def test_an_unreadable_store_is_asked_again_next_time(monkeypatch, caplog):
    activity_store.record_derived_image("squished", original="original")
    real = activity_store.derived_images

    def broken() -> dict[str, str]:
        raise activity_store.StoreUnavailableError("down")

    monkeypatch.setattr(activity_store, "derived_images", broken)
    with caplog.at_level(logging.ERROR):
        assert derived.originals() == {}
    assert "squished" in caplog.text

    monkeypatch.setattr(activity_store, "derived_images", real)
    assert derived.originals() == {"squished": "original"}


def test_carried_counts_each_copy_as_its_original_too():
    derived.record("squished", original="original")

    assert derived.carried({"squished", "plain"}) == {"squished", "original", "plain"}
