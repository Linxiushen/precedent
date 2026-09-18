# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""A rule that has gone quiet must alarm, even if it fired once long ago.

The NO_FIRES alarm was gated on `health["fires"] == 0`, and `fires` is a
lifetime count over the whole hook log with no window.  So the alarm was a
one-shot latch: the first time any rule fired, ever, it went silent for good.
The message it would have printed already claimed a window it never checked —
"nothing in 14 days" — and `daysSinceFire` was computed two lines above and
never read.

On the author's machine one rule fired once, on 2026-09-16.  From that moment
the dead-rule alarm could not have spoken again however long the rule sat
inert, which is the exact failure it exists to catch: enforcement that is
installed, counted as active in every report, and doing nothing.
"""

from datetime import datetime, timezone

import pytest

from precedent.audit import NO_FIRE_DAYS, alarms

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def _inputs(fires, days_since_fire, installed_days=90):
    fun = {"installed": True, "accepted": {"rules": ["p-1"]}}
    health = {"errors": 0, "unreadable": 0, "quarantined": 0,
              "fires": fires, "calls": 2324,
              "daysSinceFire": days_since_fire, "byRule": {}}
    hooks_status = {
        "installed": True, "drift": [],
        "receipt": {"installedAt":
                    f"2026-0{6 if installed_days > 80 else 9}-20T00:00:00Z"}}
    return fun, health, hooks_status


def _codes(state, **kw):
    fun, health, hooks = _inputs(**kw)
    return {a["code"] for a in alarms(state, [], fun, health, hooks, now=NOW)}


@pytest.fixture
def state(tmp_path):
    from precedent.state import StateDir
    st = StateDir(str(tmp_path / "state"), claude_home=str(tmp_path / "home"))
    st.ensure()
    return st


def test_a_rule_that_never_fired_alarms(state):
    assert "NO_FIRES" in _codes(state, fires=0, days_since_fire=None)


def test_a_rule_that_fired_once_long_ago_still_alarms(state):
    """The latch. One lifetime fire used to silence this forever."""
    assert "NO_FIRES" in _codes(state, fires=1, days_since_fire=40), (
        "a rule that fired once 40 days ago and nothing since did not alarm — "
        "the lifetime fire count is latching the alarm off")


def test_a_rule_that_fired_recently_does_not_alarm(state):
    assert "NO_FIRES" not in _codes(state, fires=1, days_since_fire=2)


def test_the_threshold_is_the_documented_one(state):
    assert "NO_FIRES" not in _codes(state, fires=1,
                                    days_since_fire=NO_FIRE_DAYS - 1)
    assert "NO_FIRES" in _codes(state, fires=1, days_since_fire=NO_FIRE_DAYS)


def test_the_two_cases_say_different_things(state):
    """"never fired" and "stopped firing" are different problems."""
    def msg(**kw):
        fun, health, hooks = _inputs(**kw)
        return next(a["message"] for a in alarms(state, [], fun, health, hooks,
                                                 now=NOW)
                    if a["code"] == "NO_FIRES")
    never = msg(fires=0, days_since_fire=None)
    quiet = msg(fires=1, days_since_fire=40)
    assert never != quiet
    assert "40" in quiet, quiet
    assert "ever" in never or "一次都没" in never, never
