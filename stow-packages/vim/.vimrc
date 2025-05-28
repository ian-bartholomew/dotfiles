" ------ Core ---------

set backspace=indent,eol,start   " Intuitive backspacing.
set clipboard+=unnamedplus       " Normal OS clipboard interaction
set eol                          " Add new line to end of file on save
set history=100                  " only remember 100 commands
set hlsearch                     " highlight matches
set inccommand=split             " Shows the effects of a command incrementally, as you type, with a preview
set incsearch                    " search as characters are entered
set nowrap                       " don't wrap lines
set showcmd                      " show command in bottom bar
set wildignore=*.swp,*.bak,*.pyc,*.class,*/tmp/*,*.so,*.swp,*.zip
set wildmenu                     " visual autocomplete for command menu

" ---- Indentation ------

filetype indent on      " load filetype-specific indent files
set expandtab           " tabs are spaces
set shiftwidth=2        " when using > < or =
set softtabstop=2       " number of spaces in tab when editing
set tabstop=2           " number of visual spaces per TAB
set smartindent
set autoindent

" ------ Folding --------

set foldenable          " enable folding
set foldlevelstart=10   " open most folds by default
set foldnestmax=10      " 10 nested fold max
set foldmethod=indent   " fold based on indent level

" ------ Theme ---------

set background=dark
set t_Co=256                                  " Use 256 Colors
set cursorline                                " highlight current line
set listchars=tab:>.,trail:.,extends:#,nbsp:. " highlight white spaces
set number                                    " show line numbers
set showmatch                                 " highlight matching [{()}]

" ---- Bindings -----------

" fold top level functions
nnoremap <silent> <leader>z :let&l:fdl=indent('.')/&sw<cr>
" space open/closes folds
nnoremap <space> za

nnoremap <leader>pp "0p<CR>

" vv to generate new vertical split
nnoremap <silent> vv <C-w>v
nnoremap <silent> vs <C-w>s

" Pull word under cursor into LHS of a substitute (for quick search and
" replace)
nmap <Leader>s :%s/\<<C-r><C-w>\>//g<Left><Left>

" fix for fat fingering
:command! WQ wq
:command! Wq wq
:command! Qa qa
:command! W w
:command! Q q

" yank to linux clipboard
map <Leader>y "*y
map <Leader>p "*p

"These are to cancel the default behavior of d, D, c, C
"  to put the text they delete in the default register.
"  Note that this means e.g. "ad won't copy the text into
"  register a anymore.  You have to explicitly yank it.
" nnoremap d "_d
" vnoremap d "_d
" nnoremap D "_D
" vnoremap D "_D
nnoremap c "_c
vnoremap c "_c
nnoremap C "_C
vnoremap C "_C
xnoremap p pgvy

" ------ AutoCmd ----------

" Auto clear whitespaces on save
autocmd BufWritePre * :%s/\s\+$//e
" spell check
autocmd FileType gitcommit setlocal spell
autocmd FileType yaml setlocal ts=2 sts=2 sw=2 expandtab colorcolumn=81

" ------ FileType --------

augroup filetypedetect
  " run the linter for hcl files, and reload them
  au BufWritePost *.hcl silent! !terragrunt hclfmt --terragrunt-hclfmt-file %
  au BufWritePost *.hcl edit
  au BufWritePost *.hcl redraw!
augroup END

" ------ Plugins ---------

call plug#begin('~/.vim/plugged')

" Plug 'ctrlpvim/ctrlp.vim'
" Plug 'ryanoasis/vim-devicons'
" Plug 'scrooloose/nerdtree', { 'on':  'NERDTreeToggle' }
" Plug 'tiagofumo/vim-nerdtree-syntax-highlight'
" Plug 'tpope/vim-endwise'
Plug 'MunifTanjim/nui.nvim', { 'branch': 'main' }
Plug 'nvim-lua/plenary.nvim'
Plug 'nvim-telescope/telescope.nvim'
Plug 'jackMort/ChatGPT.nvim', { 'branch': 'main' }
Plug 'MattesGroeger/vim-bookmarks'
Plug 'Raimondi/delimitMate'
Plug 'SirVer/ultisnips'
Plug 'Xuyuanp/nerdtree-git-plugin'
Plug 'Yggdroot/indentLine'
Plug 'airblade/vim-gitgutter', { 'branch': 'main' }
Plug 'aquasecurity/vim-tfsec', {'for': 'terraform'}
Plug 'dense-analysis/ale',
Plug 'elzr/vim-json', {'for': 'json'}
Plug 'fatih/vim-go', { 'do': ':GoUpdateBinaries', 'for': 'go'}
Plug 'hashivim/vim-terraform', {'for': ['terraform', 'tf', 'hcl'] }
Plug 'heavenshell/vim-jsdoc', {'for': ['javascript', 'javascript.jsx','typescript'], 'do': 'make install' }
Plug 'itspriddle/vim-shellcheck', { 'for': 'sh'}
Plug 'jvirtanen/vim-hcl', {'for': 'hcl', 'branch': 'main'}
Plug 'lepture/vim-jinja', {'for': 'jinja'}
Plug 'mileszs/ack.vim'
Plug 'morhetz/gruvbox'
Plug 'neoclide/coc.nvim', {'branch': 'release'}
Plug 'nvim-tree/nvim-tree.lua'
Plug 'nvim-tree/nvim-web-devicons'
Plug 'nvim-treesitter/nvim-treesitter', {'do': ':TSUpdate'}
Plug 'plasticboy/vim-markdown', { 'for': ['markdown', 'md']}
Plug 'preservim/tagbar'
Plug 'prettier/vim-prettier', { 'do': 'yarn install', 'for': ['javascript', 'typescript', 'typescriptreact', 'javascriptreact', 'css', 'less', 'scss', 'json', 'graphql', 'markdown', 'vue', 'svelte', 'html'] }
Plug 'sainnhe/gruvbox-material'
Plug 'scrooloose/nerdcommenter'
Plug 'sharkdp/fd'
Plug 'sheerun/vim-polyglot'
Plug 'tom-anders/telescope-vim-bookmarks.nvim', { 'branch': 'main' }
Plug 'towolf/vim-helm'
Plug 'tpope/vim-abolish'
Plug 'tpope/vim-fugitive'
Plug 'tpope/vim-obsession'
Plug 'tpope/vim-sensible'
Plug 'tpope/vim-surround'
Plug 'tpope/vim-unimpaired'
Plug 'tsandall/vim-rego', {'for': 'rego'}
Plug 'vim-airline/vim-airline'
Plug 'vim-airline/vim-airline-themes'
Plug 'vim-scripts/BufOnly.vim'
Plug 'xolox/vim-colorscheme-switcher'
Plug 'xolox/vim-misc',

" Initialize plugin system
call plug#end()

"--------- PLUGIN OPTS ---------

"" ACK
if executable('ag')
  let g:ackprg = 'ag --,m,mvimgrep'
endif

noremap <leader>a :Ack<space>
" search for the current word in ack
noremap <leader>as :Ack <C-r><C-w>

"" Airline status line
"let g:airline#extensions#tabline#enabled = 1
let g:airline_powerline_fonts = 1
" let g:airline_theme='gruvbox'
let g:airline_theme='minimalist'

"" ALE
" let g:airline#extensions#ale#enabled = 1
let js_fixers = ['prettier', 'eslint']
let ts_fixers = ['eslint']
let g:ale_linters = {
\   'javascript': js_fixers,
\   'javascript.jsx': js_fixers,
\   'yaml': ['yamllint'],
\   'python': ['pylint', 'black'],
\   'sh': ['shellcheck'] ,
\   'typescript': ts_fixers,
\   'typescriptreact': ts_fixers
\}
let g:ale_fixers = {
\   '*': ['remove_trailing_lines', 'trim_whitespace'],
\   'css': ['prettier'],
\   'hcl': ['terraform'],
\   'javascript': js_fixers,
\   'javascript.jsx': js_fixers,
\   'json': ['prettier'],
\   'python': ['black'],
\   'sh': ['shfmt'],
\   'terraform': ['terraform'],
\   'typescript': ['prettier'],
\   'typescriptreact': js_fixers,
\   'yaml': ['prettier']
\}
let g:ale_lint_on_save = 1
let g:ale_lint_on_text_changed = 0
let g:ale_fix_on_save = 1
let g:ale_echo_msg_format = 'ale: [%linter%] %s [%severity%]'
let g:ale_sign_error = '✘'
let g:ale_sign_warning = '⚠'
let g:ale_terraform_checkov_options = '--skip-check CKV_TF_1'
noremap <leader>p :ALEFix<cr>
noremap <leader>e :ALEPopulateQuickfix<cr>

"" ctrlp plugin
" set runtimepath^=~/.vim/bundle/ctrlp.vim
" let g:ctrlp_match_window = 'bottom,order:ttb'
" let g:ctrlp_working_path_mode = '0'
" let g:ctrlp_show_hidden = 1
" let g:ctrlp_max_files = 0
" let g:ctrlp_custom_ignore ='pdf\|tmp\|.DS_Store\|.sass-cache\|lcov-report\|bower_components\|cache-loader\|capistrano\|circle-ci\|github\|vscode\|log\|node_modules\|\v[\/]\.(git|hg|svn)$'
" nmap - :CtrlP<CR>
" nmap _ :CtrlPBuffer<CR>

" Find files using Telescope command-line sugar.
nnoremap <leader>ff <cmd>Telescope find_files<cr>
nnoremap - <cmd>Telescope find_files<cr>
nnoremap <leader>fg <cmd>Telescope live_grep<cr>
nnoremap <leader>fb <cmd>Telescope buffers<cr>
nnoremap _ <cmd>Telescope buffers<cr>
nnoremap <leader>fh <cmd>Telescope help_tags<cr>
nnoremap <leader>fm <cmd>Telescope vim_bookmarks all<cr>

"" vim-json plugin
let g:vim_json_syntax_conceal = 0

"" vim-prettier plugin
let g:prettier#exec_cmd_async = 1
" when running at every change you may want to disable quickfix
let g:prettier#quickfix_enabled = 0
" run before save async
let g:prettier#autoformat = 0
" single quotes over double quotes
let g:prettier#config#single_quote = 'false'
let g:prettier#config#trailing_comma = 'es5'
let g:prettier#config#bracket_spacing = 'true'

"" TagBar
noremap <Leader>t :TagbarToggle<CR>
let g:tagbar_type_ruby = {
    \ 'kinds' : [
        \ 'm:modules',
        \ 'c:classes',
        \ 'd:describes',
        \ 'C:contexts',
        \ 'f:methods',
        \ 'F:singleton methods'
    \ ]
\ }


"" COC.VIM

" TextEdit might fail if hidden is not set.
set hidden

" Some servers have issues with backup files, see #649.
set nowritebackup

" Give more space for displaying messages.
set cmdheight=2

" Use tab for trigger completion with characters ahead and navigate.
" NOTE: Use command ':verbose imap <tab>' to make sure tab is not mapped by
" other plugin before putting this into your config.
inoremap <silent><expr> <TAB>
      \ pumvisible() ? "\<C-n>" :
      \ <SID>check_back_space() ? "\<TAB>" :
      \ coc#refresh()
inoremap <expr><S-TAB> pumvisible() ? "\<C-p>" : "\<C-h>"

function! s:check_back_space() abort
  let col = col('.') - 1
  return !col || getline('.')[col - 1]  =~# '\s'
endfunction

" Use <c-space> to trigger completion.
if has('nvim')
  inoremap <silent><expr> <c-space> coc#refresh()
else
  inoremap <silent><expr> <c-@> coc#refresh()
endif

" Make <CR> auto-select the first completion item and notify coc.nvim to
" format on enter, <cr> could be remapped by other vim plugin
inoremap <silent><expr> <CR> pumvisible() ? coc#_select_confirm() : "\<C-g>u\<CR>\<c-r>=coc#on_enter()\<CR>"


" Use `[g` and `]g` to navigate diagnostics
" Use `:CocDiagnostics` to get all diagnostics of current buffer in location list.
nmap <silent> [g <Plug>(coc-diagnostic-prev)
nmap <silent> ]g <Plug>(coc-diagnostic-next)

" GoTo code navigation.
nmap <silent> gd <Plug>(coc-definition)
nmap <silent> gy <Plug>(coc-type-definition)
nmap <silent> gi <Plug>(coc-implementation)
nmap <silent> gr <Plug>(coc-references)

" Use K to show documentation in preview window.
nnoremap <silent> K :call <SID>show_documentation()<CR>

function! s:show_documentation()
  if (index(['vim','help'], &filetype) >= 0)
    execute 'h '.expand('<cword>')
  elseif (coc#rpc#ready())
    call CocActionAsync('doHover')
  else
    execute '!' . &keywordprg . " " . expand('<cword>')
  endif
endfunction "

" Highlight the symbol and its references when holding the cursor.
autocmd CursorHold * silent call CocActionAsync('highlight')

" Applying codeAction to the selected region.
" Example: `<leader>aap` for current paragraph
" xmap <leader>a  <Plug>(coc-codeaction-selected)
" nmap <leader>a  <Plug>(coc-codeaction-selected)

" Remap keys for applying codeAction to the current buffer.
nmap <leader>ac  <Plug>(coc-codeaction)
" Apply AutoFix to problem on the current line.
nmap <leader>qf  <Plug>(coc-fix-current)

" Add `:Format` command to format current buffer.
command! -nargs=0 Format :call CocAction('format')

" Add `:OR` command for organize imports of the current buffer.
command! -nargs=0 OR   :call     CocAction('runCommand', 'editor.action.organizeImport')

"" NERDCommenter
" Create default mappings
let g:NERDCreateDefaultMappings = 1

" Add spaces after comment delimiters by default
let g:NERDSpaceDelims = 1

" Use compact syntax for prettified multi-line comments
let g:NERDCompactSexyComs = 1

" Align line-wise comment delimiters flush left instead of following code indentation
let g:NERDDefaultAlign = 'left'

" Allow commenting and inverting empty lines (useful when commenting a region)
let g:NERDCommentEmptyLines = 1

" Enable trimming of trailing whitespace when uncommenting
let g:NERDTrimTrailingWhitespace = 1

" Enable NERDCommenterToggle to check all selected lines is commented or not
let g:NERDToggleCheckAllLines = 1

let g:UltiSnipsExpandTrigger = "<nop>"

"" markdown
" don't hide the special characters
let g:vim_markdown_conceal = 0
let g:vim_markdown_conceal_code_blocks = 0

"" vim-go
let g:go_fmt_command="gopls"
let g:go_gopls_gofumpt=1
let g:go_metalinter_command = "golangci-lint"

au Filetype go nmap <leader>ga <Plug>(go-alternate-edit)
au Filetype go nmap <leader>gah <Plug>(go-alternate-split)
au Filetype go nmap <leader>gav <Plug>(go-alternate-vertical)
au FileType go nmap <F9> :GoCoverageToggle -short<cr>
au FileType go nmap <F10> :GoTest -short<cr>
au FileType go nmap <F12> <Plug>(go-def)

lua << EOF
require'nvim-treesitter.configs'.setup {
  highlight = {
    enable = true,
    disable = {"vim"}
  }
}

vim.g.loaded_netrw = 1
vim.g.loaded_netrwPlugin = 1

-- set termguicolors to enable highlight groups
vim.opt.termguicolors = true

-- empty setup using defaults
require("nvim-tree").setup({
  renderer = {
    root_folder_label = false,
  },
  filters = {
    dotfiles = true,
  },
  view = {
    width = 40,
    side = "left",
  },
})

require('telescope').load_extension('vim_bookmarks')

require("chatgpt").setup({
    chat = {
        keymaps = {
            close = { "jk", "kj", "<Esc>" },
            yank_last = "<C-y>",
            scroll_up = "<C-u>",
            scroll_down = "<C-d>",
            toggle_settings = "<C-o>",
            new_session = "<C-n>",
            cycle_windows = "<Tab>",
        },
    },
    popup_input = {
        submit = "<C-s>",
    },
    openai_params = {
        models = "gpt-4-turbo-preview"
    }
})


EOF

highlight NvimTreeEndOfBuffer guibg=#2d2d2d
hi NvimTreeNormal guibg=#2d2d2d

" Make sure we set terraform files right
autocmd BufNewFile,BufRead *.tf set filetype=terraform
autocmd BufNewFile,BufRead *.tf set syntax=terraform

vmap <silent> <leader>aa :<C-U>:ChatGPTEditWithInstructions<CR>
