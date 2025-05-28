return {
	"nvimtools/none-ls.nvim",
	config = function()
		local augroup = vim.api.nvim_create_augroup("LspFormatting", {})
		local null_ls = require("null-ls")
		null_ls.setup({
			on_attach = function(client, bufnr)
				if client.supports_method("textDocument/formatting") then
					vim.api.nvim_clear_autocmds({ group = augroup, buffer = bufnr })
					vim.api.nvim_create_autocmd("BufWritePre", {
						group = augroup,
						buffer = bufnr,
						callback = function()
							vim.lsp.buf.format({
								bufnr = bufnr,
								filter = function(cl)
									return cl.name == "null-ls"
								end,
							})
						end,
					})
				end
			end,
			sources = {
				null_ls.builtins.completion.spell.with({
					filetypes = { "markdown" },
				}),
				null_ls.builtins.diagnostics.codespell,
				null_ls.builtins.formatting.codespell,
				-- null_ls.builtins.code_actions.textlint,
				null_ls.builtins.diagnostics.markdownlint,
				-- null_ls.builtins.diagnostics.shellcheck,
				null_ls.builtins.diagnostics.terraform_validate,
				null_ls.builtins.diagnostics.tfsec,
				null_ls.builtins.diagnostics.yamllint,
				null_ls.builtins.formatting.hclfmt,
				null_ls.builtins.formatting.markdownlint,
				null_ls.builtins.formatting.prettier,
				null_ls.builtins.formatting.shfmt,
				null_ls.builtins.formatting.stylua,
				null_ls.builtins.formatting.terraform_fmt,
				null_ls.builtins.formatting.gofmt,
				null_ls.builtins.formatting.black,
				-- null_ls.builtins.formatting.yamlfmt,
			},
		})

		vim.keymap.set("n", "<leader>gf", vim.lsp.buf.format, {})
	end,
}
