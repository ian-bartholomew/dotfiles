# Ian's Dotfiles

Personal dotfiles for macOS development environment, managed with GNU Stow.

## Overview

This repository contains configuration files for various development tools and applications. It uses GNU Stow for symlink management, organizing configurations into modular packages that can be independently installed or removed.

## Quick Start

```sh
# Install dependencies
brew install git stow

# Clone the repository
git clone https://github.com/ian-bartholomew/dotfiles.git ~/.dotfiles
cd ~/.dotfiles

# Install all packages
stow-packages/bootstrap
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
~/.dotfiles/stow-packages/bootstrap
```

Remove all packages:
```sh
~/.dotfiles/stow-packages/unstow
```

### Dependencies

Core dependencies are managed through:
- **Brewfile**: Homebrew packages and applications

## Requirements

- macOS
- [Homebrew](https://brew.sh/)
- GNU Stow (`brew install stow`)

## Installation

1. **Install Homebrew** (if not already installed):
   ```sh
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **Install Git and Stow**:
   ```sh
   brew install git stow
   ```

3. **Clone this repository**:
   ```sh
   git clone https://github.com/ian-bartholomew/dotfiles.git ~/.dotfiles
   ```

4. **Install configurations**:
   ```sh
   cd ~/.dotfiles
   stow-packages/bootstrap
   ```

5. **Install additional dependencies**:
   ```sh
   # Install Homebrew packages
   brew bundle --file=Brewfile
   ```

## Customization

- **Local configurations**: Create `~/.localrc` for environment variables and local settings
- **Git configuration**: The bootstrap script will prompt for your Git author name and email
- **Zsh plugins**: Managed through zgen, automatically installed on first shell startup

## Key Features

- **Modular design**: Each tool has its own stow package
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