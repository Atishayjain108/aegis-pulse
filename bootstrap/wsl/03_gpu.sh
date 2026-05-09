#!/usr/bin/env bash
# =============================================================================
# AEGIS PULSE OMEGA v2 — 03_gpu.sh
# -----------------------------------------------------------------------------
# Installs the CUDA 12.4 *WSL-Ubuntu* toolkit and cuDNN 9 inside Ubuntu, then
# runs a PyTorch smoke test. Falls back to CPU-only mode if:
#   - the host has no NVIDIA GPU, OR
#   - nvidia-smi is not reachable from WSL (driver < 2024 on Windows), OR
#   - the user passes --cpu-only.
#
# IMPORTANT: The NVIDIA DRIVER itself must be installed on Windows, NOT in WSL.
# This script only installs the CUDA user-space toolkit inside Ubuntu. The
# kernel-side GPU driver comes through the WSLg / DXGI pipe from Windows.
#
# Flags:
#   --cpu-only        Skip CUDA entirely. Config marks GPU unavailable.
#   --force-gpu       Attempt CUDA install even if nvidia-smi probe failed.
# =============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
# shellcheck source=./lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

aegis_enable_errtrap
require_wsl
require_not_root

CPU_ONLY=0
FORCE_GPU=0
for arg in "$@"; do
  case "$arg" in
    --cpu-only)  CPU_ONLY=1 ;;
    --force-gpu) FORCE_GPU=1 ;;
    -h|--help)
      sed -n '3,20p' "$0"; exit 0 ;;
    *) log_warn "Unknown flag: $arg" ;;
  esac
done

log_section "AEGIS Bootstrap — Phase 03: GPU / CUDA"

# -----------------------------------------------------------------------------
# Detect GPU
# -----------------------------------------------------------------------------
AEGIS_HAS_GPU=0
if [[ "$CPU_ONLY" == "1" ]]; then
  log_info "--cpu-only requested. Skipping CUDA install."
elif [[ "$FORCE_GPU" == "1" ]]; then
  log_info "--force-gpu: will attempt CUDA install regardless of probe."
  AEGIS_HAS_GPU=1
else
  if cmd_exists nvidia-smi && nvidia-smi -L >/dev/null 2>&1; then
    AEGIS_HAS_GPU=1
    log_success "GPU detected: $(nvidia-smi -L | head -1)"
  else
    log_warn "nvidia-smi not available inside WSL — assuming no GPU."
    log_warn "If you DO have an NVIDIA GPU:"
    log_warn "  1. Install/update the NVIDIA Windows driver (v546+)."
    log_warn "  2. From PowerShell: wsl --shutdown"
    log_warn "  3. Re-run this script with --force-gpu"
  fi
fi

state_set "has_gpu" "$AEGIS_HAS_GPU"
config_set "AEGIS_GPU_ENABLED" "$AEGIS_HAS_GPU"

if [[ "$AEGIS_HAS_GPU" != "1" ]]; then
  log_info "Installing CPU-only PyTorch for the smoke test."
  # Still run a minimal torch install to verify the Python env is healthy.
  # shellcheck disable=SC2317  # function IS called via run_step below; shellcheck can't trace indirect invocation
  install_cpu_torch() {
    local tmpd
    tmpd=$(mktemp -d)
    (
      cd "$tmpd" || exit 1
      uv venv --python "$AEGIS_PYTHON_VERSION" .venv >/dev/null
      # shellcheck disable=SC1091
      source .venv/bin/activate
      # CPU wheel — much smaller than the CUDA wheel.
      uv pip install --quiet --index-url "https://download.pytorch.org/whl/cpu" "torch==2.4.1"
      python - <<'PY'
import torch
assert not torch.cuda.is_available(), "CUDA should be absent on CPU-only path"
x = torch.randn(128, 128)
y = x @ x.T
assert y.shape == (128, 128)
print("CPU torch OK:", torch.__version__)
PY
    )
    rm -rf "$tmpd"
  }
  run_step "install_cpu_torch" install_cpu_torch

  log_section "Phase 03 complete (CPU-only)"
  log_success "GPU path skipped. AEGIS will run with CPU-only inference."
  log_info "To enable GPU later: install NVIDIA Windows driver, then re-run with --force-gpu."
  exit 0
fi

# -----------------------------------------------------------------------------
# CUDA toolkit install (WSL-Ubuntu variant — NO driver in package!)
# -----------------------------------------------------------------------------
# NVIDIA ships a special "wsl-ubuntu" repo that contains *only* the user-space
# libs; the kernel driver stays on Windows. Installing the regular Ubuntu repo
# here would clobber /usr/lib/wsl/lib/libcuda.so and break GPU access.
# Ref: https://docs.nvidia.com/cuda/wsl-user-guide/index.html
# -----------------------------------------------------------------------------
sudo_keep_alive
require_internet "https://developer.download.nvidia.com"

setup_cuda_repo() {
  local keyring_dst="/usr/share/keyrings/cuda-archive-keyring.gpg"
  local list_dst="/etc/apt/sources.list.d/cuda-wsl-ubuntu.list"

  if [[ -f "$keyring_dst" && -f "$list_dst" ]]; then
    log_info "CUDA apt repo already configured."
    return 0
  fi

  local pin_url="https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-wsl-ubuntu.pin"
  local key_url="https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/3bf863cc.pub"

  retry 3 5 -- sudo wget -qO /etc/apt/preferences.d/cuda-repository-pin-600 "$pin_url"
  retry 3 5 -- bash -c "curl -fsSL '$key_url' | sudo gpg --dearmor -o '$keyring_dst'"
  echo "deb [signed-by=$keyring_dst] https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/ /" \
    | sudo tee "$list_dst" >/dev/null

  retry 3 5 -- sudo apt-get update -y
}

run_step "setup_cuda_repo" setup_cuda_repo

install_cuda_toolkit() {
  # Install a specific minor version to match our PyTorch wheel selection.
  local pkg="cuda-toolkit-${AEGIS_CUDA_VERSION_MAJOR}-${AEGIS_CUDA_VERSION_MINOR}"
  retry 3 5 -- sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$pkg"

  # Add CUDA to PATH/LD_LIBRARY_PATH via profile.d (system-wide).
  sudo tee /etc/profile.d/aegis-cuda.sh >/dev/null <<EOF
# AEGIS PULSE OMEGA v2 — CUDA ${AEGIS_CUDA_VERSION_MAJOR}.${AEGIS_CUDA_VERSION_MINOR} env
export CUDA_HOME=/usr/local/cuda-${AEGIS_CUDA_VERSION_MAJOR}.${AEGIS_CUDA_VERSION_MINOR}
export PATH=\$CUDA_HOME/bin:\$PATH
export LD_LIBRARY_PATH=\$CUDA_HOME/lib64:\${LD_LIBRARY_PATH:-}
EOF
  # Source into current shell so nvcc is usable immediately below.
  # shellcheck disable=SC1091
  source /etc/profile.d/aegis-cuda.sh

  # Quick nvcc verify — non-fatal on version mismatch.
  if cmd_exists nvcc; then
    log_success "nvcc: $(nvcc --version | awk '/release/ {print $0}')"
  else
    log_warn "nvcc not on PATH after install — open a new shell or 'source /etc/profile.d/aegis-cuda.sh'."
  fi
}

run_step "install_cuda_toolkit" install_cuda_toolkit

# -----------------------------------------------------------------------------
# cuDNN 9 — meta-package from NVIDIA repo
# -----------------------------------------------------------------------------
install_cudnn() {
  # Match the cuda-${MAJ}-${MIN} runtime we just installed.
  local pkg="libcudnn9-cuda-${AEGIS_CUDA_VERSION_MAJOR}"
  local dev="libcudnn9-dev-cuda-${AEGIS_CUDA_VERSION_MAJOR}"
  retry 3 5 -- sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$pkg" "$dev"
  log_success "cuDNN 9 installed."
}

run_step "install_cudnn" install_cudnn

# -----------------------------------------------------------------------------
# PyTorch GPU smoke test
# -----------------------------------------------------------------------------
smoke_test_cuda() {
  local tmpd
  tmpd=$(mktemp -d)
  (
    cd "$tmpd" || exit 1
    uv venv --python "$AEGIS_PYTHON_VERSION" .venv >/dev/null
    # shellcheck disable=SC1091
    source .venv/bin/activate
    # PyTorch 2.4.1 + CUDA 12.4 wheel from the official index.
    uv pip install --quiet --index-url "https://download.pytorch.org/whl/cu124" \
      "torch==2.4.1" "torchvision==0.19.1"
    python - <<'PY'
import torch
print("Torch:", torch.__version__)
print("CUDA build:", torch.version.cuda)
assert torch.cuda.is_available(), (
    "CUDA wheel installed but torch.cuda.is_available() is False. "
    "Typical cause: Windows NVIDIA driver is older than CUDA 12.4 requires. "
    "Update to driver 550+ and run `wsl --shutdown`."
)
dev = torch.device("cuda:0")
print("Device name:", torch.cuda.get_device_name(dev))
x = torch.randn(4096, 4096, device=dev)
y = x @ x.T
torch.cuda.synchronize()
print("GPU matmul shape:", tuple(y.shape), "dtype:", y.dtype)
print("Allocated MB:", round(torch.cuda.memory_allocated(dev)/1024/1024, 1))
print("SMOKE OK")
PY
  )
  local rc=$?
  rm -rf "$tmpd"
  if [[ $rc -ne 0 ]]; then
    log_error "CUDA smoke test failed."
    log_warn  "Falling back to CPU-only. Run: bash $0 --cpu-only"
    state_set "has_gpu" "0"
    config_set "AEGIS_GPU_ENABLED" "0"
    return 1
  fi
  log_success "CUDA smoke test passed."
}

run_step "smoke_test_cuda" smoke_test_cuda || true  # don't abort bootstrap on GPU miss

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
log_section "Phase 03 complete"
log_info "GPU enabled:   $(state_get has_gpu)"
cmd_exists nvcc       && log_info "nvcc:          $(nvcc --version | awk '/release/{sub(/.*release /,""); print}')"
cmd_exists nvidia-smi && log_info "Driver (WSL):  $(nvidia-smi | awk '/Driver Version/ {print $3; exit}')"
log_success "Phase 03 done."
