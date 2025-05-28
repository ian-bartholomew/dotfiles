return {
	{
		"nvim-telescope/telescope-ui-select.nvim",
	},
	{
		"nvim-telescope/telescope.nvim",
		tag = "0.1.5",
		dependencies = { "nvim-lua/plenary.nvim" },
		config = function()
			require("telescope").setup({
				extensions = {
					["ui-select"] = {
						require("telescope.themes").get_dropdown({}),
					},
				},
			})
			local builtin = require("telescope.builtin")
			vim.keymap.set("n", "-", builtin.find_files, {})
			vim.keymap.set("n", "_", builtin.buffers, {})
			vim.keymap.set("n", "<leader>fh", builtin.help_tags, {})
			vim.keymap.set("n", "<leader>fg", builtin.live_grep, {})
			vim.keymap.set("n", "<leader>c", builtin.commands, {})
			vim.keymap.set("n", "<leader>ch", builtin.command_history, {})
			vim.keymap.set("n", "<leader>sh", builtin.search_history, {})
			vim.keymap.set("n", "<leader>qf", builtin.quickfix, {})
			vim.keymap.set("n", "<leader>ac", builtin.autocommands, {})
			vim.keymap.set("n", "<leader>km", builtin.keymaps, {})
			vim.keymap.set("n", "<leader>tp", builtin.pickers, {})
			vim.keymap.set("n", "<leader>sr", builtin.treesitter, {})

			require("telescope").load_extension("ui-select")
		end,
	},
}
