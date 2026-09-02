"""Constants for the Weishaupt Modbus API."""

DEFAULT_PORT = 502

# Weishaupt Hardware-level limits
MAX_BLOCK_READ_COUNT = 5


BACKOFF_BASE_SECONDS = 5 * 60  # 5 minutes
BACKOFF_MAX_SECONDS = 60 * 60  # 60 minutes
BACKOFF_THRESHOLD_FAILURES = 3

# Weishaupt rates the register EEPROM for this many writes over the pump's
# lifetime. The counters below make the consumption visible; the warning
# fires once a day at the threshold, the limit (0 = none) refuses writes
# for the rest of the day.
EEPROM_WRITE_RATING = 100_000
DEFAULT_WRITE_WARNING_PER_DAY = 50
DEFAULT_WRITE_LIMIT_PER_DAY = 0
