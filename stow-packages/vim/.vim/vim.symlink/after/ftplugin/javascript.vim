set tabstop=2
set softtabstop=2
set shiftwidth=2

" show trailing whitespace as errors
match ErrorMsg '\s\+$'
" delete trailing whitespace on save
autocmd BufWritePre * :%s/\s\+$//e

set wildignore+=node_modlues/**

" add a gutter
let &colorcolumn=join(range(81,999),",")

" show spaces as .
set lcs+=space:·

" disable the preview window in omnicomplete
"set completeopt-=preview
