return {
	"rcarriga/nvim-notify",
	event = "VeryLazy",
	opts = {
		stages = "fade",
	},
	config = function(_, opts)
		local notify = require("notify")
		notify.setup(opts)
		vim.notify = notify
	end,
}
