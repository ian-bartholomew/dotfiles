-- Core
vim.opt.clipboard = "unnamedplus" -- Normal OS clipboard interaction
vim.opt.history = 100 -- Only remember 100 commands
vim.opt.wildignore:append("*.swp,*.bak,*.pyc,*.class,*/tmp/*,*.so,*.zip")
vim.cmd("set inccommand=split") -- Shows the effects of a command incrementally, as you type, with a preview
vim.opt.swapfile = false

-- Indentation
vim.opt.expandtab = true -- Tabs are spaces
vim.opt.shiftwidth = 2 -- Width for > < =
vim.opt.softtabstop = 2 -- Spaces per tab (editing)
vim.opt.tabstop = 2 -- Visual spaces per TAB
vim.opt.wrap = false

-- Folding
vim.opt.foldlevelstart = 10 -- Open most folds by default
vim.opt.foldnestmax = 10 -- 10 nested fold max
vim.opt.foldmethod = "indent" -- Fold based on indent level

-- Theme
vim.opt.termguicolors = true -- Use GUI colors for the terminal
vim.opt.listchars = "tab:>.,trail:.,extends:#,nbsp:."
vim.opt.number = true -- Show line numbers
vim.g.mapleader = " "

-- Spelling
vim.opt.spelllang = "en_us"
vim.opt.spell = true

-- Key Bindings
local opts = { noremap = true, silent = true }

vim.keymap.set("n", "<leader>z", ':let&l:fdl=indent(".")/&sw<CR>', opts)
vim.keymap.set("n", "<leader>ff", "za", opts)
vim.keymap.set("n", "<leader>pp", '"0p<CR>', opts)
vim.keymap.set("n", "vv", "<C-w>v", opts)
vim.keymap.set("n", "vs", "<C-w>s", opts)
vim.keymap.set("n", "<Leader>s", ":%s/\\<<C-r><C-w>\\>//g<Left><Left>", { noremap = true })

-- Yank and put with system clipboard
vim.keymap.set("n", "<Leader>y", '"*y', { noremap = false })
vim.keymap.set("n", "<Leader>p", '"*p', { noremap = false })

-- Cancel default behavior of certain commands to not yank text
vim.keymap.set("n", "c", '"_c', opts)
vim.keymap.set("v", "c", '"_c', opts)
vim.keymap.set("n", "C", '"_C', opts)
vim.keymap.set("v", "C", '"_C', opts)
vim.keymap.set("x", "p", "pgvy", opts)

-- Navigate vim panes better
vim.keymap.set("n", "<c-k>", ":wincmd k<CR>")
vim.keymap.set("n", "<c-j>", ":wincmd j<CR>")
vim.keymap.set("n", "<c-h>", ":wincmd h<CR>")
vim.keymap.set("n", "<c-l>", ":wincmd l<CR>")

vim.keymap.set("n", "<leader>h", ":nohlsearch<CR>")
vim.keymap.set("n", "<leader>bb", "<C-^><CR>")

-- Custom commands for fixing common typos
vim.cmd([[
command! WQ wq
command! Wq wq
command! Qa qa
command! W w
command! Q q
]])

vim.wo.number = true

-- set termguicolors to enable highlight groups
vim.opt.termguicolors = true

-- AutoCmds for specific functionalities
vim.cmd([[
autocmd BufWritePre * :%s/\\s\\+$//e
autocmd FileType gitcommit setlocal spell
autocmd FileType yaml setlocal ts=2 sts=2 sw=2 expandtab colorcolumn=81
]])

-- FileType specific commands
vim.cmd([[
augroup filetypedetect
  au BufWritePost *.hcl silent! !terragrunt hclfmt --terragrunt-hclfmt-file %
  au BufWritePost *.hcl edit
  au BufWritePost *.hcl redraw!

augroup END
]])

-- Create or clear an existing autogroup for Terraform configurations
local terraform_group = vim.api.nvim_create_augroup("TerraformFileType", { clear = true })
-- Autocommand for setting filetype and syntax for Terraform files
vim.api.nvim_create_autocmd({ "BufNewFile", "BufRead" }, {
	group = terraform_group,
	pattern = "*.tf",
	callback = function()
		-- Set filetype and syntax for Terraform files
		vim.bo.filetype = "terraform"
		vim.bo.syntax = "terraform"
	end,
})

-- BufOnly
-- Define a Lua function to execute the buffer deletion and keep the current buffer
local function buf_only()
	-- Get the current buffer number
	local current_buf = vim.api.nvim_get_current_buf()
	-- Get the list of all buffer numbers
	local buffers = vim.api.nvim_list_bufs()

	-- Close all buffers except the current one
	for _, buf in ipairs(buffers) do
		if buf ~= current_buf and vim.api.nvim_buf_is_loaded(buf) then
			-- Delete the buffer without closing the window, force deletion without confirmation
			vim.api.nvim_buf_delete(buf, { force = true })
		end
	end

	-- Optionally, you can recenter the cursor or adjust the view as needed
	-- vim.api.nvim_command('normal! zz')
end

-- Register the command "BufOnly" that calls the buf_only function
vim.api.nvim_create_user_command("BufOnly", buf_only, {})

-- Basic keymap utility
local opts = { noremap = true, silent = true }

-- Go to definition
vim.keymap.set("n", "gd", vim.lsp.buf.definition, opts)

-- Show definition in quickfix list
vim.keymap.set("n", "<leader>ld", function()
	vim.lsp.buf.definition({
		on_list = function(opts)
			vim.fn.setqflist({}, " ", opts)
			vim.cmd("copen")
		end,
	})
end, opts)

-- List all symbols in the current document
vim.keymap.set("n", "<leader>ls", function()
	vim.lsp.buf.document_symbol({
		on_list = function(opts)
			vim.fn.setloclist(0, {}, " ", opts)
			vim.cmd("lopen")
		end,
	})
end, opts)

-- Preview definition in floating window
vim.keymap.set("n", "<leader>lp", function()
	local params = vim.lsp.util.make_position_params()
	vim.lsp.buf_request(0, "textDocument/definition", params, function(err, result, ctx, _)
		if err or not result or vim.tbl_isempty(result) then
			vim.notify("No definition found", vim.log.levels.WARN)
			return
		end
		-- Support both single and multiple results
		local location = (vim.tbl_islist(result) and result[1]) or result
		vim.lsp.util.preview_location(location, { border = "single" })
	end)
end, opts)
