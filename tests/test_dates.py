"""Tests for the deterministic date resolver. Run: python3 tests/test_dates.py"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yapcal.dates import resolve

NOW = datetime(2026, 11, 16, 14, 30)  # Monday 2:30pm
fails = 0


def check(dp, tp, dur, expect_start, ambiguous=None, label=""):
    global fails
    r = resolve(dp, tp, dur, now=NOW)
    got = r.start
    tag = f"'{dp}' '{tp}' '{dur}'"
    if got != expect_start:
        print(f"FAIL {tag}: got {got}, want {expect_start}")
        fails += 1
    elif ambiguous is not None and r.ambiguous != ambiguous:
        print(f"FAIL {tag}: ambiguous={r.ambiguous}, want {ambiguous}")
        fails += 1
    else:
        print(f"ok   {tag} -> {got}")


# relative days
check("tmr", "", "", datetime(2026, 11, 17, 9, 0), ambiguous=True)
check("tomorrow", "3pm", "", datetime(2026, 11, 17, 15, 0))
check("today", "4pm", "", datetime(2026, 11, 16, 16, 0))
check("tonight", "", "", datetime(2026, 11, 16, 20, 0))
check("in 3 days", "", "", datetime(2026, 11, 19, 9, 0), ambiguous=True)
check("next week", "", "", datetime(2026, 11, 23, 9, 0), ambiguous=True)
check("this weekend", "noon", "", datetime(2026, 11, 21, 12, 0))

# weekdays
check("friday", "6pm", "", datetime(2026, 11, 20, 18, 0))
check("next friday", "9am", "", datetime(2026, 11, 27, 9, 0))
check("monday", "8am", "", datetime(2026, 11, 23, 8, 0))

# explicit dates
check("nov 20", "11:59pm", "", datetime(2026, 11, 20, 23, 59))
check("20 Nov", "", "", datetime(2026, 11, 20, 9, 0), ambiguous=True)
check("11/25", "3", "", datetime(2026, 11, 25, 15, 0))
check("2026-12-01", "14:00", "", datetime(2026, 12, 1, 14, 0))
check("dec 25", "morning", "", datetime(2026, 12, 25, 9, 0))
check("the 20th", "5pm", "", datetime(2026, 11, 20, 17, 0))
check("the 10th", "5pm", "", datetime(2026, 12, 10, 17, 0))  # passed -> next month

# combined phrases
check("tmr at 3pm", "", "", datetime(2026, 11, 17, 15, 0))
check("friday at 6", "", "", datetime(2026, 11, 20, 18, 0))
check("on sat @ 10am", "", "", datetime(2026, 11, 21, 10, 0))

# durations
r = resolve("tmr", "3pm", "2 hours", now=NOW)
assert r.end == datetime(2026, 11, 17, 17, 0), r.end
print("ok   duration 2 hours")
r = resolve("tmr", "3pm", "90 min", now=NOW)
assert r.end == datetime(2026, 11, 17, 16, 30), r.end
print("ok   duration 90 min")

# time ranges
check("between 12am and 6am tmr", "", "", datetime(2026, 11, 17, 0, 0))
r = resolve("between 12am and 6am tmr", "", "", now=NOW)
assert r.end == datetime(2026, 11, 17, 6, 0), r.end
print("ok   range end 6am")
r = resolve("tmr 10pm to 2am", "", "", now=NOW)
assert r.start == datetime(2026, 11, 17, 22, 0) and r.end == datetime(2026, 11, 18, 2, 0), (r.start, r.end)
print("ok   range crosses midnight")
r = resolve("fri 9 to 5", "", "", now=NOW)
assert r.start == datetime(2026, 11, 20, 9, 0) and r.end == datetime(2026, 11, 20, 17, 0), (r.start, r.end)
print("ok   work hours 9 to 5")
r = resolve("2026-12-01", "14:00", "", now=NOW)
assert r.start == datetime(2026, 12, 1, 14, 0)  # ISO date not mistaken for a range
print("ok   ISO date safe from range regex")

# no-"at" time suffixes (found via bot test)
check("tmr 3pm", "", "", datetime(2026, 11, 17, 15, 0))
check("dec 14 2pm", "", "", datetime(2026, 12, 14, 14, 0))
check("friday 6", "", "", datetime(2026, 11, 20, 18, 0))

# ambiguity paths
r = resolve("someday", "", "", now=NOW)
assert r.ambiguous and not r.ok
print("ok   unparseable date -> ambiguous")
r = resolve("", "", "", now=NOW)
assert r.ambiguous  # no date -> today 9am flagged
print("ok   empty -> today 9am, flagged")

print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURES'}")

def test_all():  # pytest entry point
    assert fails == 0, f"{fails} failures (see stdout)"

if __name__ == "__main__":
    sys.exit(1 if fails else 0)
