"""A lock that tells when something waits for it, for the shared-lock tests.

Such a test holds the controller's lock and checks that nothing reaches the
controller meanwhile. Waiting a fixed time for that catches a broken lock
only if the request comes within the window: under load it came later, and a
mutation handing the web interface a lock of its own went through
(2026-10-03). Waiting until the task blocks on the lock, or got past it, has
no window to miss.
"""

import asyncio
from collections.abc import Callable


class WatchedLock(asyncio.Lock):
    """An asyncio lock that records that someone had to wait for it."""

    def __init__(self) -> None:
        super().__init__()
        self.waited = False

    async def acquire(self) -> bool:
        if self.locked():
            self.waited = True
        return await super().acquire()


# Long enough for any task here to reach the lock, short next to the cut-off
# of a hung test.
UNTIL_SECONDS = 5.0


async def until(
    condition: Callable[[], object], seconds: float = UNTIL_SECONDS
) -> None:
    """Let the event loop run until `condition()` holds, for `seconds` at most.

    Without a limit, a lock wired wrong left the test waiting for a waiter
    that never came, until pytest cut the run off without saying why.
    """
    try:
        async with asyncio.timeout(seconds):
            while not condition():
                await asyncio.sleep(0)
    except TimeoutError:
        raise AssertionError(
            f"what the test waited for never came in {seconds} s"
        ) from None
