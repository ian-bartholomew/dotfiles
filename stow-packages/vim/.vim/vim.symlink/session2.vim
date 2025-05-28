let SessionLoad = 1
let s:so_save = &so | let s:siso_save = &siso | set so=0 siso=0
let v:this_session=expand("<sfile>:p")
silent only
cd ~/Dev/sweetgreen-dev-slack
if expand('%') == '' && !&modified && line('$') <= 1 && getline(1) == ''
  let s:wipebuf = bufnr('%')
endif
set shortmess=aoO
badd +1 ~/Dev/sweetgreen-dev-slack
badd +104 serverless.yml
badd +1 handler.js
badd +12 package.json
badd +0 .eslintrc.json
argglobal
silent! argdel *
$argadd ~/Dev/sweetgreen-dev-slack
edit .eslintrc.json
set splitbelow splitright
wincmd _ | wincmd |
vsplit
wincmd _ | wincmd |
vsplit
2wincmd h
wincmd w
wincmd w
set nosplitbelow
set nosplitright
wincmd t
set winminheight=1 winminwidth=1 winheight=1 winwidth=1
exe 'vert 1resize ' . ((&columns * 31 + 73) / 146)
exe 'vert 2resize ' . ((&columns * 41 + 73) / 146)
exe 'vert 3resize ' . ((&columns * 72 + 73) / 146)
argglobal
enew
file NERD_tree_1
setlocal fdm=manual
setlocal fde=0
setlocal fmr={{{,}}}
setlocal fdi=#
setlocal fdl=10
setlocal fml=1
setlocal fdn=10
setlocal nofen
lcd ~/Dev/sweetgreen-dev-slack
wincmd w
argglobal
setlocal fdm=indent
setlocal fde=0
setlocal fmr={{{,}}}
setlocal fdi=#
setlocal fdl=10
setlocal fml=1
setlocal fdn=10
setlocal fen
let s:l = 1 - ((0 * winheight(0) + 31) / 62)
if s:l < 1 | let s:l = 1 | endif
exe s:l
normal! zt
1
let s:c = 22 - ((12 * winwidth(0) + 20) / 41)
if s:c > 0
  exe 'normal! ' . s:c . '|zs' . 22 . '|'
else
  normal! 022|
endif
lcd ~/Dev/sweetgreen-dev-slack
wincmd w
argglobal
if bufexists('~/Dev/sweetgreen-dev-slack/serverless.yml') | buffer ~/Dev/sweetgreen-dev-slack/serverless.yml | else | edit ~/Dev/sweetgreen-dev-slack/serverless.yml | endif
setlocal fdm=indent
setlocal fde=0
setlocal fmr={{{,}}}
setlocal fdi=#
setlocal fdl=10
setlocal fml=1
setlocal fdn=10
setlocal fen
58
normal! zo
59
normal! zo
let s:l = 61 - ((18 * winheight(0) + 31) / 62)
if s:l < 1 | let s:l = 1 | endif
exe s:l
normal! zt
61
normal! 013|
lcd ~/Dev/sweetgreen-dev-slack
wincmd w
2wincmd w
exe 'vert 1resize ' . ((&columns * 31 + 73) / 146)
exe 'vert 2resize ' . ((&columns * 41 + 73) / 146)
exe 'vert 3resize ' . ((&columns * 72 + 73) / 146)
tabnext 1
if exists('s:wipebuf') && getbufvar(s:wipebuf, '&buftype') isnot# 'terminal'
  silent exe 'bwipe ' . s:wipebuf
endif
unlet! s:wipebuf
set winheight=1 winwidth=20 winminheight=1 winminwidth=1 shortmess=filnxtToOFc
let s:sx = expand("<sfile>:p:r")."x.vim"
if file_readable(s:sx)
  exe "source " . fnameescape(s:sx)
endif
let &so = s:so_save | let &siso = s:siso_save
doautoall SessionLoadPost
unlet SessionLoad
" vim: set ft=vim :
