from datetime import datetime, timedelta, timezone
from typing import Tuple

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def window_bounds(now: datetime, window_seconds: int) -> Tuple[datetime, int]:
    """Fixed-window start for `now` and the seconds remaining until the window ends."""
    elapsed = int((now - _EPOCH).total_seconds())
    start_ts = elapsed - (elapsed % window_seconds)
    start = _EPOCH + timedelta(seconds=start_ts)
    retry_after = max(1, window_seconds - (elapsed - start_ts))
    return start, retry_after
