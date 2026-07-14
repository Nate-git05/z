#!/usr/bin/env bash
# Z installer — install the Z coding agent CLI.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Nate-git05/z/main/install.sh | sh
#
# Optional env:
#   Z_REPO_URL   — git URL to install from (default: https://github.com/Nate-git05/z.git)
#   Z_PIP        — pip executable to use (default: auto-detect)
#   Z_NO_MODIFY_PATH — set to 1 to skip writing PATH into shell rc files

set -euo pipefail

BOLD=$'\033[1m'
DIM=$'\033[2m'
ACCENT=$'\033[38;2;201;106;43m' # #C96A2B
RESET=$'\033[0m'

info() { printf '%s\n' "${DIM}>${RESET} $*"; }
ok() { printf '%s\n' "${ACCENT}✓${RESET} $*"; }
err() { printf '%s\n' "${BOLD}error:${RESET} $*" >&2; exit 1; }

Z_REPO_URL="${Z_REPO_URL:-https://github.com/Nate-git05/z.git}"

detect_os() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || echo unknown)"
  case "$uname_s" in
    Linux*) echo linux ;;
    Darwin*) echo macos ;;
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    *) echo "$uname_s" ;;
  esac
}

detect_arch() {
  local uname_m
  uname_m="$(uname -m 2>/dev/null || echo unknown)"
  case "$uname_m" in
    x86_64|amd64) echo x86_64 ;;
    aarch64|arm64) echo arm64 ;;
    *) echo "$uname_m" ;;
  esac
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || err "Missing required command: $1"
}

find_python() {
  local candidates=(python3 python)
  local c ver major minor
  for c in "${candidates[@]}"; do
    if command -v "$c" >/dev/null 2>&1; then
      ver="$("$c" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)"
      if [[ -n "$ver" ]]; then
        major="${ver%%.*}"
        minor="${ver#*.}"
        if [[ "$major" -gt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -ge 10 ]]; }; then
          echo "$c"
          return 0
        fi
      fi
    fi
  done
  return 1
}

find_pip() {
  if [[ -n "${Z_PIP:-}" ]]; then
    echo "$Z_PIP"
    return 0
  fi
  local py="$1"
  if "$py" -m pip --version >/dev/null 2>&1; then
    echo "$py -m pip"
    return 0
  fi
  if command -v pip3 >/dev/null 2>&1; then
    echo pip3
    return 0
  fi
  if command -v pip >/dev/null 2>&1; then
    echo pip
    return 0
  fi
  return 1
}

scripts_bin_dir() {
  local py="$1"
  "$py" -c 'import sysconfig; print(sysconfig.get_path("scripts"))' 2>/dev/null \
    || "$py" -c 'import site, os; print(os.path.join(site.USER_BASE, "bin"))'
}

ensure_path_line() {
  local bin_dir="$1"
  local line="export PATH=\"${bin_dir}:\$PATH\"  # z-agent"
  local rc=""
  local shell_name
  shell_name="$(basename "${SHELL:-bash}")"

  case "$shell_name" in
    zsh) rc="${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash)
      if [[ "$(detect_os)" == "macos" ]]; then
        rc="$HOME/.bash_profile"
      else
        rc="$HOME/.bashrc"
      fi
      ;;
    fish)
      # Fish uses a different syntax; print manual instructions instead.
      info "Fish detected — add this to your config:"
      info "  fish_add_path ${bin_dir}"
      return 0
      ;;
    *) rc="$HOME/.profile" ;;
  esac

  if [[ "${Z_NO_MODIFY_PATH:-0}" == "1" ]]; then
    info "Skipping PATH update (Z_NO_MODIFY_PATH=1)."
    info "Add to your shell profile: export PATH=\"${bin_dir}:\$PATH\""
    return 0
  fi

  mkdir -p "$(dirname "$rc")"
  touch "$rc"
  if grep -Fq "z-agent" "$rc" 2>/dev/null || grep -Fq "$bin_dir" "$rc" 2>/dev/null; then
    info "PATH already configured in ${rc}"
    return 0
  fi
  {
    printf '\n# Z coding agent\n'
    printf '%s\n' "$line"
  } >>"$rc"
  ok "Added ${bin_dir} to PATH in ${rc}"
  info "Restart your shell or run: source ${rc}"
}

main() {
  local os arch py pip_cmd bin_dir

  printf '%s\n' "${BOLD}${ACCENT}Z${RESET} ${DIM}coding agent installer${RESET}"
  echo

  os="$(detect_os)"
  arch="$(detect_arch)"
  info "Detected ${os}/${arch}"

  if [[ "$os" == "windows" ]]; then
    err "Native Windows install isn't supported yet. Use WSL (Ubuntu) and re-run this script."
  fi

  py="$(find_python)" || err "Python 3.10+ is required. Install python3 and re-run."
  info "Using $($py --version 2>&1)"

  pip_cmd="$(find_pip "$py")" || err "pip not found. Try: ${py} -m ensurepip --upgrade"
  info "Using pip via: ${pip_cmd}"

  # Ensure pip can install user/scripts packages
  # Prefer an upgrade of pip itself (best-effort)
  # shellcheck disable=SC2086
  $pip_cmd install --upgrade pip >/dev/null 2>&1 || true

  info "Installing Z from ${Z_REPO_URL} …"
  # shellcheck disable=SC2086
  if ! $pip_cmd install --upgrade "git+${Z_REPO_URL}"; then
    # Fallback: user install if system site-packages is locked down
    info "Retrying with --user …"
    # shellcheck disable=SC2086
    $pip_cmd install --user --upgrade "git+${Z_REPO_URL}" || err "Install failed."
  fi

  bin_dir="$(scripts_bin_dir "$py")"
  export PATH="${bin_dir}:${HOME}/.local/bin:$PATH"

  if ! command -v z >/dev/null 2>&1; then
    err "Installed, but 'z' is not on PATH. Tried: ${bin_dir} and ~/.local/bin"
  fi

  # Prefer the directory that actually contains the z binary for PATH updates
  z_path="$(command -v z)"
  bin_dir="$(dirname "$z_path")"

  ensure_path_line "$bin_dir"

  echo
  ok "Z installed: $(command -v z)"
  info "Try:  z models"
  info "Then: export ANTHROPIC_API_KEY=…   # or OPENAI_API_KEY"
  info "      cd your-project && z"
  echo
}

main "$@"
