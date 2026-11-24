return {
	"nvim-lualine/lualine.nvim",
	config = function()
		require("lualine").setup({
			options = {
				theme = "gruvbox-material",
			},
			sections = {
				lualine_z = { "%l/%L:%c" },
			},
		})
	end,
}
