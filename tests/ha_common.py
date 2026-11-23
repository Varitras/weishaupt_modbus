"""Home Assistant's test helpers, from whichever test plugin this run installed.

Home Assistant ships its own plugin, pytest-homeassistant, from 2026.11 on.
The custom-component plugin it replaces ended with 2026.10 and stays only for
the lane on the declared minimum; drop the fallback once that reaches 2026.11.
"""

try:
    from pytest_homeassistant.common import (
        MockConfigEntry,
        async_capture_events,
        async_fire_time_changed,
        mock_restore_cache_with_extra_data,
    )
    from pytest_homeassistant.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )
except ImportError:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_capture_events,
        async_fire_time_changed,
        mock_restore_cache_with_extra_data,
    )
    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )

__all__ = [
    "MockConfigEntry",
    "async_capture_events",
    "async_fire_time_changed",
    "get_diagnostics_for_config_entry",
    "mock_restore_cache_with_extra_data",
]
