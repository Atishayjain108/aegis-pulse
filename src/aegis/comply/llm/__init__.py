"""Optional LLM augmentation layer (Phase 11 connectivity)."""

from __future__ import annotations

from aegis.comply.llm.augmentor import (
    ComplianceAugmentor,
    LLMComplete,
    build_default_augmentor,
)

__all__ = [
    "ComplianceAugmentor",
    "LLMComplete",
    "build_default_augmentor",
]
