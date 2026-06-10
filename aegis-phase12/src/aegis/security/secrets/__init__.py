"""aegis.security.secrets — Layered secret retrieval (Vault → SOPS → env) and rotation."""

from aegis.security.secrets.manager import SecretsManager
from aegis.security.secrets.rotation import RotationRecord, SecretRotationManager

__all__ = ["RotationRecord", "SecretRotationManager", "SecretsManager"]
