"""
ONNX export pipeline.

Why ONNX
--------
The serving layer must hit p99 < 500 ms on a CPU. PyTorch eager
inference is fast enough at single-instance batch=1, but we want to
keep the runtime container free of the full torch dependency in
production. ONNX Runtime is ~5x smaller, has predictable latency, and
supports INT8 quantization via dynamic quantization for the linear
layers — typically 2-4x faster on CPU at < 1% accuracy loss.

Doctrine compliance: if torch / onnx / onnxruntime are missing the
export is a no-op that returns `False`. The serving layer always has
a heuristic fallback.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


try:  # pragma: no cover
    import torch

    _HAS_TORCH = True
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    _HAS_TORCH = False


try:  # pragma: no cover
    import onnx  # noqa: F401

    _HAS_ONNX = True
except ImportError:  # pragma: no cover
    _HAS_ONNX = False


def export_to_onnx(
    network: Any,
    output_path: str | Path,
    *,
    input_shape: tuple[int, ...] = (1, 168, 20),
    opset_version: int = 17,
    quantize_int8: bool = True,
    dynamic_axes: dict | None = None,
) -> bool:
    """Export `network` to ONNX, optionally INT8-quantized.

    Returns True on success, False on (silent) failure. Logs warnings
    on every failure path so an operator can investigate.

    Args:
        network: nn.Module in eval mode.
        output_path: where to write the .onnx file.
        input_shape: example input — (batch, length, features) for
                     temporal models, (batch, ...) for graph nets.
        opset_version: ONNX opset. 17 is the highest stable as of 2026.
        quantize_int8: enable dynamic INT8 quantization (linear layers).
        dynamic_axes: dict for marking dimensions as dynamic. Defaults
                     to making the batch dim dynamic so inference can
                     batch up to whatever the runtime decides.
    """
    if not _HAS_TORCH:
        logger.warning("export_to_onnx: torch unavailable — skipping")
        return False
    if not _HAS_ONNX:
        logger.warning("export_to_onnx: onnx unavailable — skipping")
        return False

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if dynamic_axes is None:
        dynamic_axes = {"input": {0: "batch"}, "output": {0: "batch"}}

    try:
        network.eval()
        dummy = torch.zeros(input_shape, dtype=torch.float32)
        with torch.no_grad():
            tmp_path = output_path
            torch.onnx.export(
                network,
                dummy,
                str(tmp_path),
                input_names=["input"],
                output_names=["output"],
                opset_version=opset_version,
                dynamic_axes=dynamic_axes,
                do_constant_folding=True,
            )
        logger.info("exported ONNX → %s", tmp_path)
    except Exception as exc:  # pragma: no cover — guard rail
        logger.error("ONNX export failed: %s", exc)
        return False

    if quantize_int8:
        try:
            from onnxruntime.quantization import (  # type: ignore
                QuantType,
                quantize_dynamic,
            )

            quantized_path = str(output_path).replace(".onnx", ".int8.onnx")
            quantize_dynamic(
                model_input=str(output_path),
                model_output=quantized_path,
                weight_type=QuantType.QInt8,
            )
            logger.info("quantized ONNX → %s", quantized_path)
        except Exception as exc:  # pragma: no cover
            logger.warning("INT8 quantization failed: %s — keeping fp32", exc)

    return True


def load_onnx_session(model_path: str | Path) -> Any:
    """Load an ONNX model into an inference session.

    Returns None if onnxruntime is missing. Caller should fall back to
    the heuristic predictor in that case.
    """
    try:  # pragma: no cover
        import onnxruntime as ort
    except ImportError:  # pragma: no cover
        logger.warning("onnxruntime unavailable — cannot load %s", model_path)
        return None

    if not Path(model_path).exists():
        logger.warning("ONNX model not found: %s", model_path)
        return None

    try:  # pragma: no cover
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
        return ort.InferenceSession(str(model_path), sess_opts, providers=["CPUExecutionProvider"])
    except Exception as exc:
        logger.error("ONNX session creation failed: %s", exc)
        return None
