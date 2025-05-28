set tabstop=4 
set softtabstop=4 
set shiftwidth=4 
set textwidth=79 
set expandtab 
set autoindent 
set fileformat=unix

"show trailing whitespace as errors
match ErrorMsg '\s\+$'
" delete trailing whitespace on save 
autocmd BufWritePre * :%s/\s\+$//e

" add a gutter 
let &colorcolumn=join(range(81,999),",")

