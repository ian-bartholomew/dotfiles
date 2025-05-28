return {
	"numToStr/Comment.nvim",
	lazy = false,
	config = function()
		local ft = require("Comment.ft")
		ft.hcl = { "#%s", "/*%s*/" }
		require("Comment").setup()
	end,
}
