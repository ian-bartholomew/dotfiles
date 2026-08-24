# Ian's Dotfiles

Personal dotfiles for macOS, Arch Linux, and Ubuntu, managed with GNU Stow.

## Overview

This repository contains configuration files for various development tools and applications. It uses GNU Stow for symlink management, organizing configurations into modular packages that can be independently installed or removed.

## Quick Start

```sh
# Clone the repository
git clone https://github.com/ian-bartholomew/dotfiles.git ~/.dotfiles
cd ~/.dotfiles

# Bootstrap everything: fetches the dotctl helper, installs dependencies,
# stows all packages, configures git, and switches your shell to zsh
stow-packages/bootstrap.sh
```

## Structure

### Stow Packages

Each directory in `stow-packages/` represents a configuration package:

- **git**: Git configuration (layered .gitconfig, .gitignore, allowed_signers)
- **zsh**: Shell configuration (.zshrc) and modular zsh files
- **vim**: Legacy Vim configuration (.vimrc and .vim directory)
- **nvim**: Neovim configuration (in .config/nvim/)
- **tmux**: Terminal multiplexer configuration (.tmux.conf)
- **ssh**: SSH client configuration (.ssh/config)
- **atuin**: Shell history manager configuration
- **claude**: Claude Code user config (CLAUDE.md and skills/)

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

System dependencies are defined in `packages.csv`, a single shared list with per-platform package names. `bootstrap.sh` fetches a pinned, checksum-verified `dotctl` helper that installs them for your platform:

- **macOS**: `brew` (installs Homebrew if needed)
- **Arch Linux**: `pacman` (with `yay` for AUR packages)
- **Ubuntu/Debian**: `apt-get`

Required packages install automatically. To add optional categories or individual packages, run `dotctl install` with `--categories`, `--packages`, or `--all`.

## Requirements

- macOS, Arch Linux, or Ubuntu/Debian
- `git` and `bash`

Everything else (including the package manager on macOS) is handled by `bootstrap.sh`.

## Installation

1. **Clone this repository**:

   ```sh
   git clone https://github.com/ian-bartholomew/dotfiles.git ~/.dotfiles
   cd ~/.dotfiles
   ```

2. **Run the bootstrap**:

   ```sh
   stow-packages/bootstrap.sh
   ```

   This fetches the `dotctl` helper, installs dependencies from `packages.csv`, stows all packages, configures git (prompting for your email), and switches your default shell to zsh.

## Customization

- **Local configurations**: Create `~/.localrc` for environment variables and local settings
- **Git configuration**: The bootstrap script prompts for your Git email; name and shared settings live in the committed base config, per-machine overrides in `~/.config/git/config.machine`
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
