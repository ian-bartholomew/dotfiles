#!/usr/bin/env bash
#
# unstow removes dotfiles symlinks using GNU Stow.

cd "$(dirname "$0")"

set -e

echo ''

info () {
  printf "\r  [ \033[00;34m..\033[0m ] $1\n"
}

success () {
  printf "\r\033[2K  [ \033[00;32mOK\033[0m ] $1\n"
}

fail () {
  printf "\r\033[2K  [\033[0;31mFAIL\033[0m] $1\n"
  echo ''
  exit
}

check_stow () {
  if ! command -v stow &> /dev/null
  then
    fail "GNU Stow is not installed. Install it with: brew install stow"
  fi
}

unstow_dotfiles () {
  info 'removing dotfiles with stow'

  # Unstow each package, but skip if it's ignored by git
  for package in */
  do
    if [ -d "$package" ]; then
      package_name=$(basename "$package")
      
      # Check if package is ignored by git
      if git -C .. check-ignore "$package" >/dev/null 2>&1; then
        info "skipping ignored package: $package_name"
        continue
      fi
      
      info "unstowing $package_name"
      stow -v -D -t "$HOME" --ignore="\.DS_Store" "$package_name"
      success "unstowed $package_name"
    fi
  done
}

check_stow
unstow_dotfiles

echo ''
echo '  All removed!'