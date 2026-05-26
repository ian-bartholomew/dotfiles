return {
	"numToStr/Comment.nvim",
	lazy = false,
	config = function()
		local ft = require("Comment")
		ft.hcl = { "#%s", "/*%s*/" }
		ft.setup()
	end,
}
