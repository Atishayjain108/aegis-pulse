"""aegis.security.vault — HashiCorp Vault integration package."""

from aegis.security.vault._circuit_breaker import CircuitBreaker
from aegis.security.vault._errors import VaultError, VaultErrorCode
from aegis.security.vault.bootstrap import VaultBootstrap, bootstrap_vault
from aegis.security.vault.client import VaultClient, VaultSecret

__all__ = [
    "CircuitBreaker",
    "VaultBootstrap",
    "VaultClient",
    "VaultError",
    "VaultErrorCode",
    "VaultSecret",
    "bootstrap_vault",
]
