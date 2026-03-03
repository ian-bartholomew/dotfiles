# Ian's Dotfiles

Personal dotfiles for macOS, Arch Linux, and Ubuntu, managed with GNU Stow.

## Overview

This repository contains configuration files for various development tools and applications. It uses GNU Stow for symlink management, organizing configurations into modular packages that can be independently installed or removed.

## Quick Start

```sh
# Clone the repository
git clone https://github.com/ian-bartholomew/dotfiles.git ~/.dotfiles
cd ~/.dotfiles

# Install system dependencies (detects your platform automatically)
./install.sh

# Install all dotfile packages
stow-packages/bootstrap.sh

# Or do both in one step
stow-packages/bootstrap.sh --install-deps
```

## Structure

### Stow Packages

Each directory in `stow-packages/` represents a configuration package:

- **git**: Git configuration (.gitconfig, .gitignore)
- **zsh**: Shell configuration (.zshrc) and modular zsh files
- **vim**: Legacy Vim configuration (.vimrc and .vim directory)
- **nvim**: Neovim configuration (in .config/nvim/)
- **tmux**: Terminal multiplexer configuration (.tmux.conf)
- **asdf**: Version manager configuration (.tool-versions)
- **atuin**: Shell history manager configuration

### Package Management

Install specific packages:
```sh
cd ~/.dotfiles/stow-packages
stow nvim     # Install Neovim config to ~/.config/nvim/
stow zsh      # Install zsh config to ~/.zshrc and ~/.config/zsh/
stow git      # Install git config to ~/.gitconfig and ~/.gitignore
```

Remove packages:
```sh
cd ~/.dotfiles/stow-packages
stow -D nvim  # Remove Neovim config symlinks
```

Install all packages:
```sh
~/.dotfiles/stow-packages/bootstrap.sh
```

Remove all packages:
```sh
~/.dotfiles/stow-packages/unstow.sh
```

### Dependencies

System dependencies are defined in `packages.csv` — a single shared list with per-platform package names. Run `./install.sh` to install them for your platform:

- **macOS**: Generates a Brewfile and runs `brew bundle` (installs Homebrew if needed)
- **Arch Linux**: Installs via `pacman` and `yay` (installs yay if needed)
- **Ubuntu/Debian**: Installs via `apt-get`

## Requirements

- macOS, Arch Linux, or Ubuntu/Debian
- `git` and `bash`

Everything else (including the package manager on macOS) is handled by `install.sh`.

## Installation

1. **Clone this repository**:
   ```sh
   git clone https://github.com/ian-bartholomew/dotfiles.git ~/.dotfiles
   cd ~/.dotfiles
   ```

2. **Install system dependencies**:
   ```sh
   ./install.sh
   ```

3. **Install configurations**:
   ```sh
   stow-packages/bootstrap.sh
   ```

## Customization

- **Local configurations**: Create `~/.localrc` for environment variables and local settings
- **Git configuration**: The bootstrap script will prompt for your Git author name and email
- **Zsh plugins**: Managed through zgen, automatically installed on first shell startup

## Key Features

- **Modular design**: Each tool has its own stow package
- **Cross-platform**: Single package list works on macOS, Arch Linux, and Ubuntu
- **Neovim configuration**: Full Lua-based config with lazy.nvim plugin manager
- **Shell enhancements**: Zsh with oh-my-zsh, spaceship prompt, and useful plugins
- **Development tools**: Git aliases, tmux configuration, and version management
- **Shell history**: Enhanced with atuin for better command history

## Troubleshooting

- **Stow conflicts**: If stow reports conflicts, remove existing dotfiles or use `stow -D` to remove old symlinks
- **Zsh issues**: Ensure `~/.config/zsh/` contains the modular zsh files after stowing
- **Plugin issues**: Delete `~/.zgen/` to regenerate zsh plugins

## License

MIT License - see LICENSE.md
