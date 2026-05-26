# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Neovim configuration managed as a GNU Stow package within a dotfiles repo (`~/.dotfiles/stow-packages/nvim/`). It deploys to `~/.config/nvim/` via stow. The config uses **lazy.nvim** as the plugin manager.

## Architecture

- `init.lua` — Bootstrap: installs lazy.nvim if missing, loads `vim-options`, then calls `lazy.setup("plugins")` which auto-discovers all files under `lua/plugins/`
- `lua/vim-options.lua` — Core settings, keymaps, autocmds, and custom commands (leader is `<Space>`)
- `lua/plugins/*.lua` — Each file returns a lazy.nvim plugin spec (single table or list of tables). Lazy auto-loads every file in this directory.
- `lua/plugins/codecompanion/fidget-spinner.lua` — Helper module required by CodeCompanion (not a lazy plugin spec)
- `lua/plugins.lua` — Empty; exists as a placeholder (lazy discovers `plugins/` directory instead)
- `coc-settings.json` — Legacy CoC config; LSP has migrated to native `nvim-lspconfig` + Mason
- `spell/` — Custom spell dictionary

## Key Patterns

**Adding a new plugin:** Create a new file in `lua/plugins/` returning a lazy.nvim spec table. Lazy auto-discovers it — no registration needed elsewhere.

**LSP setup:** Mason auto-installs servers listed in `mason-lspconfig` (`lsp-config.lua`). Server configs use the newer `vim.lsp.config()` / `vim.lsp.enable()` API (not the older `lspconfig.server.setup()` pattern). LSP keymaps are set in both `vim-options.lua` (global `gd`, `<leader>ld`, `<leader>ls`, `<leader>lp`) and `lsp-config.lua` (buffer-local via `LspAttach`).

**Formatting:** none-ls (null-ls successor) handles format-on-save via `BufWritePre` autocmd, filtering to only use null-ls formatters. Configured formatters: stylua (Lua), prettier (JS/HTML), terraform_fmt, hclfmt, gofmt, black (Python), shfmt, markdownlint.

**Autocmds of note:**

- Trailing whitespace is stripped on every save (`BufWritePre`)
- HCL files are auto-formatted with `terragrunt hclfmt` on save
- `.tf` files get `terraform` filetype/syntax detection

## Languages Supported

Lua, TypeScript/JavaScript, Go, Python, Terraform/HCL, Bash, YAML, HTML, Markdown — reflected in LSP servers, treesitter, formatters, and DAP configs.
