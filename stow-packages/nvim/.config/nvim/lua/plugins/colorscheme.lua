return {
  -- {
  --   "catppuccin/nvim",
  --   lazy = false,
  --   name = "catppuccin",
  --   priority = 1000,
  --   config = function()
  --     vim.cmd.colorscheme("catppuccin-macchiato")
  --   end,
  -- }
  {
    "EdenEast/nightfox.nvim",
    config = function()
      vim.cmd("colorscheme nordfox")
    end,
  }, -- lazy
  -- {
  --   "morhetz/gruvbox",
  --   priority = 1000,
  --   config = function()
  --     vim.cmd.colorscheme "gruvbox"
  --   end
  -- }
  -- {
  --   "sainnhe/gruvbox-material",
  --   priority = 1000,
  --   config = function()
  --     vim.cmd.colorscheme "gruvbox-material"
  --   end
  -- }
  -- {
  -- 	"folke/tokyonight.nvim",
  -- 	lazy = false,
  -- 	priority = 1000,
  -- 	config = function()
  -- 		vim.cmd.colorscheme("tokyonight-storm")
  -- 	end,
  -- },
}
