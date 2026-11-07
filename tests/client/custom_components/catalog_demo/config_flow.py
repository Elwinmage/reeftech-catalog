"""Config flow of the demo integration (single step, no option)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult


class DemoConfigFlow(ConfigFlow, domain="catalog_demo"):
    """Create an entry without asking anything."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry."""
        return self.async_create_entry(title="Catalog demo", data={})
