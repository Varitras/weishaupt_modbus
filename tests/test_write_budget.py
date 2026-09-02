"""The EEPROM write counters."""

from datetime import date

from custom_components.weishaupt_modbus.weishaupt_modbus_api.write_budget import (
    WriteBudget,
)

MONDAY = date(2026, 9, 7)
TUESDAY = date(2026, 9, 8)


class Clock:
    def __init__(self, day):
        self.day = day

    def __call__(self):
        return self.day


def test_writes_are_counted_in_total_and_today():
    budget = WriteBudget(warn_at=0, limit=0, today=Clock(MONDAY))

    budget.record_write()
    budget.record_write()

    assert (budget.total, budget.writes_today) == (2, 2)


def test_the_daily_count_starts_over_at_midnight_and_the_total_does_not():
    clock = Clock(MONDAY)
    budget = WriteBudget(warn_at=0, limit=0, today=clock)
    budget.record_write()

    clock.day = TUESDAY

    assert (budget.total, budget.writes_today, budget.day) == (1, 0, TUESDAY)


def test_the_warning_fires_exactly_once_when_the_threshold_is_reached():
    budget = WriteBudget(warn_at=2, limit=0, today=Clock(MONDAY))

    assert [budget.record_write() for _ in range(4)] == [False, True, False, False]


def test_a_threshold_of_zero_never_warns():
    budget = WriteBudget(warn_at=0, limit=0, today=Clock(MONDAY))

    assert not any(budget.record_write() for _ in range(3))


def test_the_limit_closes_the_day_and_a_new_day_opens_it():
    clock = Clock(MONDAY)
    budget = WriteBudget(warn_at=0, limit=1, today=clock)
    assert budget.allows_write()
    budget.record_write()

    assert not budget.allows_write()
    clock.day = TUESDAY
    assert budget.allows_write()


def test_a_limit_of_zero_means_no_limit():
    budget = WriteBudget(warn_at=0, limit=0, today=Clock(MONDAY))
    for _ in range(1000):
        budget.record_write()

    assert budget.allows_write()


def test_a_listener_hears_every_counted_write_until_it_unsubscribes():
    budget = WriteBudget(warn_at=0, limit=0, today=Clock(MONDAY))
    heard = []
    unsubscribe = budget.add_listener(lambda: heard.append(budget.total))

    budget.record_write()
    budget.record_write()
    unsubscribe()
    budget.record_write()

    assert heard == [1, 2]


def test_a_restored_count_from_yesterday_is_stale():
    budget = WriteBudget(warn_at=0, limit=0, today=Clock(TUESDAY))

    budget.restore_today(7, MONDAY)
    assert budget.writes_today == 0

    budget.restore_today(7, TUESDAY)
    assert budget.writes_today == 7
