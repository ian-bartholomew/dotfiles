return {
  "nvim-neo-tree/neo-tree.nvim",
  branch = "v3.x",
  dependencies = {
    "nvim-lua/plenary.nvim",
    "nvim-tree/nvim-web-devicons",
    "MunifTanjim/nui.nvim",
  },
  config = function()
    vim.keymap.set("n", "<C-n>", ":Neotree filesystem reveal left<CR>", {})
    vim.keymap.set("n", ",m", ":Neotree toggle<CR>", {})
    vim.keymap.set("n", ",n", ":Neotree reveal<CR>", {})
    vim.keymap.set("n", "<leader>bf", ":Neotree buffers reveal float<CR>", {})

    local neo_tree = require("neo-tree")
    neo_tree.setup({
      filesystem = {
        filtered_items = {
          visible = true,
          hide_dotfiles = false,
          hide_gitignored = true,
          hide_by_name = {
            ".github",
            ".gitignore",
            "package-lock.json",
            ".DS_Store",
          },
          never_show = { -- remains hidden even if visible is toggled to true, this overrides always_show
            ".DS_Store",
            ".git",
            "thumbs.db",
            ".terraform",
            ".terragrunt-cache",
          },
        },
      },
      window = {
        mappings = {
          ["P"] = function(state)
            local node = state.tree:get_node()
            require("neo-tree.ui.renderer").focus_node(state, node:get_parent_id())
          end,
        },
      },
    })
  end,
}
