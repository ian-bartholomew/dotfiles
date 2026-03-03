#!/usr/bin/env bash
#
# Cross-platform package installer
# Reads packages.csv and installs via brew (macOS), pacman/yay (Arch), or apt (Ubuntu/Debian).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PACKAGES_CSV="$SCRIPT_DIR/packages.csv"

# --- Logging helpers ---

info() {
  printf "\r  [ \033[00;34m..\033[0m ] %s\n" "$1"
}

success() {
  printf "\r\033[2K  [ \033[00;32mOK\033[0m ] %s\n" "$1"
}

warn() {
  printf "\r\033[2K  [ \033[0;33m!!\033[0m ] %s\n" "$1"
}

fail() {
  printf "\r\033[2K  [\033[0;31mFAIL\033[0m] %s\n" "$1"
}

# --- Globals ---

PLATFORM=""
DRY_RUN=false
FAILED_PACKAGES=()
SELECTED_CATEGORIES=()

# Package list (built once, used for display + install)
PKG_CATEGORIES=()  # category per package
PKG_NAMES=()       # platform-specific name (with AUR:/CASK: prefix)

# --- Argument parsing ---

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --dry-run    Show what would be installed without actually installing
  -h, --help   Show this help message
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=true ;;
      -h|--help) usage; exit 0 ;;
      *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
    shift
  done
}

# --- Platform detection ---

detect_platform() {
  case "$OSTYPE" in
    darwin*)
      PLATFORM="macos"
      ;;
    linux*)
      if [[ ! -f /etc/os-release ]]; then
        echo "Cannot detect Linux distribution (/etc/os-release not found)." >&2
        exit 1
      fi

      # shellcheck source=/dev/null
      source /etc/os-release

      case "$ID" in
        arch|endeavouros|manjaro)
          PLATFORM="arch"
          ;;
        ubuntu|debian|pop|linuxmint)
          PLATFORM="ubuntu"
          ;;
        *)
          # Check ID_LIKE as a fallback for derivatives
          case "${ID_LIKE:-}" in
            *arch*)   PLATFORM="arch" ;;
            *debian*) PLATFORM="ubuntu" ;;
            *)
              echo "Unsupported Linux distribution: $ID ($PRETTY_NAME)" >&2
              exit 1
              ;;
          esac
          ;;
      esac
      ;;
    *)
      echo "Unsupported OS: $OSTYPE" >&2
      exit 1
      ;;
  esac

  success "Detected platform: $PLATFORM"
}

# --- Category helpers ---

# Get unique categories from packages.csv
get_categories() {
  local seen=""
  while IFS='|' read -r category _rest; do
    [[ "$category" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$category" ]] && continue
    category="$(echo "$category" | xargs)"
    if [[ "|$seen|" != *"|$category|"* ]]; then
      echo "$category"
      seen="$seen|$category"
    fi
  done < "$PACKAGES_CSV"
}

# Count packages available on current platform for a category
count_packages_in_category() {
  local target_category="$1"
  local count=0

  while IFS='|' read -r category brew_name pacman_name apt_name notes; do
    [[ "$category" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$category" ]] && continue
    category="$(echo "$category" | xargs)"
    [[ "$category" != "$target_category" ]] && continue

    local pkg=""
    case "$PLATFORM" in
      macos)  pkg="$(echo "$brew_name" | xargs)" ;;
      arch)   pkg="$(echo "$pacman_name" | xargs)" ;;
      ubuntu) pkg="$(echo "$apt_name" | xargs)" ;;
    esac
    [[ "$pkg" != "-" ]] && ((count++))
  done < "$PACKAGES_CSV"

  echo "$count"
}

# Check if a category was selected
category_selected() {
  local cat="$1"
  for c in "${SELECTED_CATEGORIES[@]}"; do
    [[ "$c" == "$cat" ]] && return 0
  done
  return 1
}

# Prompt user to choose categories
select_categories() {
  local categories=()
  while IFS= read -r cat; do
    categories+=("$cat")
  done < <(get_categories)

  echo ""
  info "Available package categories:"
  echo ""
  printf "    %s) %s\n" "0" "all"
  for i in "${!categories[@]}"; do
    local cat="${categories[$i]}"
    local count
    count=$(count_packages_in_category "$cat")
    printf "    %s) %s (%d packages)\n" "$((i + 1))" "$cat" "$count"
  done

  echo ""
  printf "  [ \033[0;33m??\033[0m ] Select categories to install (e.g. 0,1,3 or 'all') [0]: "
  read -r selection

  # Default to all
  selection="${selection:-0}"

  if [[ "$selection" == "0" || "$selection" == "all" ]]; then
    SELECTED_CATEGORIES=("${categories[@]}")
    return
  fi

  # Parse comma-separated numbers
  IFS=',' read -ra nums <<< "$selection"
  for num in "${nums[@]}"; do
    num="$(echo "$num" | xargs)"
    if [[ "$num" =~ ^[0-9]+$ ]] && (( num >= 1 && num <= ${#categories[@]} )); then
      SELECTED_CATEGORIES+=("${categories[$((num - 1))]}")
    else
      warn "Ignoring invalid selection: $num"
    fi
  done

  if [[ ${#SELECTED_CATEGORIES[@]} -eq 0 ]]; then
    fail "No valid categories selected"
    exit 1
  fi
}

# --- Package list ---

# Build the resolved package list for the current platform + selected categories
build_package_list() {
  PKG_CATEGORIES=()
  PKG_NAMES=()

  while IFS='|' read -r category brew_name pacman_name apt_name notes; do
    [[ "$category" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$category" ]] && continue

    category="$(echo "$category" | xargs)"
    category_selected "$category" || continue

    local pkg=""
    case "$PLATFORM" in
      macos)  pkg="$(echo "$brew_name" | xargs)" ;;
      arch)   pkg="$(echo "$pacman_name" | xargs)" ;;
      ubuntu) pkg="$(echo "$apt_name" | xargs)" ;;
    esac
    [[ "$pkg" == "-" ]] && continue

    PKG_CATEGORIES+=("$category")
    PKG_NAMES+=("$pkg")
  done < "$PACKAGES_CSV"
}

# Format a package name for display (strip prefix, add annotation)
format_pkg_display() {
  local pkg="$1"
  if [[ "$pkg" == AUR:* ]]; then
    echo "${pkg#AUR:} (AUR)"
  elif [[ "$pkg" == CASK:* ]]; then
    echo "${pkg#CASK:} (cask)"
  else
    echo "$pkg"
  fi
}

# Display numbered package list grouped by category
display_package_list() {
  local current_category=""
  for i in "${!PKG_NAMES[@]}"; do
    [[ -z "${PKG_NAMES[$i]}" ]] && continue

    if [[ "${PKG_CATEGORIES[$i]}" != "$current_category" ]]; then
      current_category="${PKG_CATEGORIES[$i]}"
      echo "    [$current_category]"
    fi

    local display
    display="$(format_pkg_display "${PKG_NAMES[$i]}")"
    printf "      %3d) %s\n" "$((i + 1))" "$display"
  done
}

# Prompt user to exclude packages by number
exclude_packages() {
  echo ""
  printf "  [ \033[0;33m??\033[0m ] Exclude any packages? Enter numbers (e.g. 3,7,12) or press Enter to skip: "
  read -r exclusions

  [[ -z "$exclusions" ]] && return

  IFS=',' read -ra nums <<< "$exclusions"
  for num in "${nums[@]}"; do
    num="$(echo "$num" | xargs)"
    if [[ "$num" =~ ^[0-9]+$ ]] && (( num >= 1 && num <= ${#PKG_NAMES[@]} )); then
      local idx=$((num - 1))
      if [[ -n "${PKG_NAMES[$idx]}" ]]; then
        local display
        display="$(format_pkg_display "${PKG_NAMES[$idx]}")"
        info "Excluding: $display"
        PKG_NAMES[$idx]=""
      fi
    else
      warn "Ignoring invalid selection: $num"
    fi
  done
}

# --- Confirmation ---

confirm_install() {
  echo ""
  info "Install summary:"
  echo ""
  echo "    Platform:   $PLATFORM"
  if $DRY_RUN; then
    echo "    Mode:       dry run (no changes will be made)"
  fi
  echo "    Categories: ${SELECTED_CATEGORIES[*]}"
  echo ""

  display_package_list
  exclude_packages

  # Count remaining packages
  local count=0
  for pkg in "${PKG_NAMES[@]}"; do
    [[ -n "$pkg" ]] && count=$((count + 1))
  done

  if [[ "$count" -eq 0 ]]; then
    info "No packages to install."
    exit 0
  fi

  echo ""
  printf "  [ \033[0;33m??\033[0m ] Install %d packages? [Y/n]: " "$count"
  read -r answer

  case "${answer:-y}" in
    [Yy]*) return 0 ;;
    *)
      info "Aborted."
      exit 0
      ;;
  esac
}

# --- Package manager wrappers ---

install_pacman() {
  local pkg="$1"
  if $DRY_RUN; then
    info "[dry run] pacman -S $pkg"
    return 0
  fi
  if pacman -Qi "$pkg" &>/dev/null; then
    info "$pkg already installed"
    return 0
  fi
  if sudo pacman -S --noconfirm --needed "$pkg"; then
    success "Installed $pkg (pacman)"
  else
    warn "Failed to install $pkg (pacman)"
    FAILED_PACKAGES+=("$pkg")
  fi
}

install_aur() {
  local pkg="$1"
  if $DRY_RUN; then
    info "[dry run] yay -S $pkg"
    return 0
  fi
  if yay -Qi "$pkg" &>/dev/null; then
    info "$pkg already installed"
    return 0
  fi
  if yay -S --noconfirm --needed "$pkg"; then
    success "Installed $pkg (yay)"
  else
    warn "Failed to install $pkg (yay)"
    FAILED_PACKAGES+=("$pkg")
  fi
}

install_apt() {
  local pkg="$1"
  if $DRY_RUN; then
    info "[dry run] apt-get install $pkg"
    return 0
  fi
  if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
    info "$pkg already installed"
    return 0
  fi
  if sudo apt-get install -y "$pkg"; then
    success "Installed $pkg (apt)"
  else
    warn "Failed to install $pkg (apt)"
    FAILED_PACKAGES+=("$pkg")
  fi
}

# --- Arch: ensure yay is available ---

ensure_yay() {
  if command -v yay &>/dev/null; then
    return 0
  fi

  if $DRY_RUN; then
    info "[dry run] Would install yay from AUR"
    return 0
  fi

  info "Installing yay (AUR helper)..."
  install_pacman git
  install_pacman base-devel

  local tmpdir
  tmpdir="$(mktemp -d)"
  git clone https://aur.archlinux.org/yay.git "$tmpdir/yay"
  (cd "$tmpdir/yay" && makepkg -si --noconfirm)
  rm -rf "$tmpdir"

  if command -v yay &>/dev/null; then
    success "yay installed"
  else
    fail "Failed to install yay"
    exit 1
  fi
}

# --- macOS: ensure Homebrew is available ---

ensure_homebrew() {
  if command -v brew &>/dev/null; then
    return 0
  fi

  if $DRY_RUN; then
    info "[dry run] Would install Homebrew"
    return 0
  fi

  info "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  if command -v brew &>/dev/null; then
    success "Homebrew installed"
  else
    fail "Failed to install Homebrew"
    exit 1
  fi
}

# --- macOS installer ---

install_macos() {
  ensure_homebrew

  info "Generating Brewfile from packages.csv..."
  local brewfile current_category=""
  brewfile="$(mktemp)"

  echo "cask_args appdir: '/Applications'" > "$brewfile"
  echo "" >> "$brewfile"

  for i in "${!PKG_NAMES[@]}"; do
    local pkg="${PKG_NAMES[$i]}"
    [[ -z "$pkg" ]] && continue

    local category="${PKG_CATEGORIES[$i]}"
    if [[ "$category" != "$current_category" ]]; then
      [[ -n "$current_category" ]] && echo "" >> "$brewfile"
      echo "# $category" >> "$brewfile"
      current_category="$category"
    fi

    if [[ "$pkg" == CASK:* ]]; then
      echo "cask '${pkg#CASK:}'" >> "$brewfile"
    else
      echo "brew '$pkg'" >> "$brewfile"
    fi
  done

  if $DRY_RUN; then
    info "[dry run] Would run: brew bundle --file=<generated>"
    echo ""
    echo "--- Generated Brewfile ---"
    cat "$brewfile"
    echo "--- End Brewfile ---"
    rm -f "$brewfile"
    return 0
  fi

  info "Running brew bundle..."
  if brew bundle --file="$brewfile"; then
    success "brew bundle complete"
  else
    warn "brew bundle finished with errors (some packages may have failed)"
    FAILED_PACKAGES+=("(see brew bundle output above)")
  fi

  rm -f "$brewfile"
}

# --- Linux installer (Arch or Ubuntu/Debian) ---

install_linux_packages() {
  local pm="$1" # "arch" or "ubuntu"

  if ! $DRY_RUN; then
    if [[ "$pm" == "arch" ]]; then
      info "Syncing pacman database..."
      sudo pacman -Sy
    elif [[ "$pm" == "ubuntu" ]]; then
      info "Updating apt package lists..."
      sudo apt-get update
    fi
  fi

  if [[ "$pm" == "arch" ]]; then
    ensure_yay
  fi

  for i in "${!PKG_NAMES[@]}"; do
    local pkg="${PKG_NAMES[$i]}"
    [[ -z "$pkg" ]] && continue

    # Handle AUR packages
    if [[ "$pkg" == AUR:* ]]; then
      local aur_pkg="${pkg#AUR:}"
      if [[ "$pm" == "arch" ]]; then
        install_aur "$aur_pkg"
      else
        warn "Skipping AUR package $aur_pkg (not available on Ubuntu)"
      fi
      continue
    fi

    # Standard package install
    if [[ "$pm" == "arch" ]]; then
      install_pacman "$pkg"
    else
      install_apt "$pkg"
    fi
  done
}

# --- Main ---

main() {
  parse_args "$@"

  echo ""
  info "Cross-platform package installer"
  if $DRY_RUN; then
    warn "Dry run mode — no packages will be installed"
  fi
  echo ""

  if [[ ! -f "$PACKAGES_CSV" ]]; then
    fail "packages.csv not found at $PACKAGES_CSV"
    exit 1
  fi

  detect_platform
  select_categories
  build_package_list
  confirm_install

  case "$PLATFORM" in
    macos)
      install_macos
      ;;
    arch)
      install_linux_packages arch
      ;;
    ubuntu)
      install_linux_packages ubuntu
      ;;
  esac

  # Summary
  echo ""
  if $DRY_RUN; then
    success "Dry run complete — no changes were made"
  elif [[ ${#FAILED_PACKAGES[@]} -eq 0 ]]; then
    success "All packages installed successfully!"
  else
    warn "The following packages failed to install:"
    for pkg in "${FAILED_PACKAGES[@]}"; do
      echo "    - $pkg"
    done
  fi
  echo ""
}

main "$@"
