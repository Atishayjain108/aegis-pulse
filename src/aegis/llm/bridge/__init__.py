"""aegis.llm.bridge — integration bridges to other phases."""

from aegis.llm.bridge.agents_bridge import (
    close_gateway,
    complete_for_agent,
    get_gateway,
    set_gateway,
)

__all__ = ["close_gateway", "complete_for_agent", "get_gateway", "set_gateway"]
