#!/usr/bin/env bash
#
# bootstrap installs things using GNU Stow.

cd "$(dirname "$0")/.."
DOTFILES_ROOT=$(pwd -P)

set -e

# Install system dependencies if requested
if [[ "${1:-}" == "--install-deps" ]]; then
  "$DOTFILES_ROOT/install.sh"
fi

echo ''

info () {
  printf "\r  [ \033[00;34m..\033[0m ] $1\n"
}

user () {
  printf "\r  [ \033[0;33m??\033[0m ] $1\n"
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
    local hint="./install.sh or your package manager"
    case "$(uname -s)" in
      Darwin) hint="brew install stow" ;;
      Linux)
        if command -v pacman &>/dev/null; then
          hint="sudo pacman -S stow"
        elif command -v apt-get &>/dev/null; then
          hint="sudo apt-get install stow"
        fi
        ;;
    esac
    fail "GNU Stow is not installed. Install it with: $hint"
  fi
}

setup_gitconfig () {
  if ! [ -f stow-packages/git/.gitconfig ]
  then
    info 'setup gitconfig'

    git_credential='cache'
    if [ "$(uname -s)" == "Darwin" ]
    then
      git_credential='osxkeychain'
    fi

    user ' - What is your github author name?'
    read -e git_authorname
    user ' - What is your github author email?'
    read -e git_authoremail

    sed -e "s/AUTHORNAME/$git_authorname/g" -e "s/AUTHOREMAIL/$git_authoremail/g" -e "s/GIT_CREDENTIAL_HELPER/$git_credential/g" git/gitconfig.symlink.example > stow-packages/git/.gitconfig

    success 'gitconfig'
  fi
}

install_dotfiles () {
  info 'installing dotfiles with stow'

  cd stow-packages
  
  # Stow each package, but skip if it's ignored by git
  for package in */
  do
    package_name=$(basename "$package")
    
    # Check if package is ignored by git
    if git -C .. check-ignore "$package" >/dev/null 2>&1; then
      info "skipping ignored package: $package_name"
      continue
    fi
    
    info "stowing $package_name"
    stow -v -t "$HOME" --ignore="\.DS_Store" "$package_name"
    success "stowed $package_name"
  done
  
  cd ..
}

check_stow
setup_gitconfig
install_dotfiles

echo ''
echo '  All installed!'