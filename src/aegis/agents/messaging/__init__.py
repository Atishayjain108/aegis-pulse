"""Inter-agent messaging over Redis Streams with HMAC integrity."""
from .hmac_sign import sign, verify
from .streams import AgentBus, BusConsumer

__all__ = ["AgentBus", "BusConsumer", "sign", "verify"]
