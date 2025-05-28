# zmodload zsh/zprof
# shortcut to this dotfiles path is $ZSH
export DOTFILES=$HOME/.dotfiles
export ZSH=$DOTFILES

# your project folder that we can `c [tab]` to
export PROJECTS=~/Dev

# set default user
export DEFAULT_USER=`whoami`

# all of our zsh files
typeset -U config_files
config_files=($HOME/.config/zsh/*.zsh)

# load the path files
for file in ${(M)config_files:#*/path.zsh}
do
  source $file
done

# load everything but the alias, path and completion files
for file in ${${config_files:#*/path.zsh}:#*/aliases.zsh:#*/completion.zsh}
do
  source $file
done

# initialize autocomplete here, otherwise functions won't be loaded
## completion stuff
zstyle ':compinstall' filename '$HOME/.zshrc'

zcachedir="$HOME/.zcache"
[[ -d "$zcachedir" ]] || mkdir -p "$zcachedir"

_update_zcomp() {
    setopt local_options
    setopt extendedglob
    autoload -Uz compinit
    local zcompf="$1/zcompdump"
    # use a separate file to determine when to regenerate, as compinit doesn't
    # always need to modify the compdump
    local zcompf_a="${zcompf}.augur"

    if [[ -e "$zcompf_a" && -f "$zcompf_a"(#qN.md-1) ]]; then
        compinit -C -d "$zcompf"
    else
        compinit -d "$zcompf"
        touch "$zcompf_a"
    fi
    # if zcompdump exists (and is non-zero), and is older than the .zwc file,
    # then regenerate
    if [[ -s "$zcompf" && (! -s "${zcompf}.zwc" || "$zcompf" -nt "${zcompf}.zwc") ]]; then
        # since file is mapped, it might be mapped right now (current shells), so
        # rename it then make a new one
        [[ -e "$zcompf.zwc" ]] && mv -f "$zcompf.zwc" "$zcompf.zwc.old"
        # compile it mapped, so multiple shells can share it (total mem reduction)
        # run in background
        zcompile -M "$zcompf" &!
    fi
}
_update_zcomp "$zcachedir"
unfunction _update_zcomp

# load every completion after autocomplete loads
for file in ${(M)config_files:#*/completion.zsh}
do
  source $file
done


unset config_files

# Load zgen only if a user types a zgen command
zgen () {
  if [[ ! -s ${ZDOTDIR:-${HOME}}/.zgen/zgen.zsh ]]; then
    git clone --recursive https://github.com/tarjoilija/zgen.git ${ZDOTDIR:-${HOME}}/.zgen
  fi
  source ${ZDOTDIR:-${HOME}}/.zgen/zgen.zsh
  zgen "$@"
}
export NVM_LAZY_LOAD=true
export NVM_COMPLETION=true

# check if there's no init script
if [[ ! -s ${ZDOTDIR:-${HOME}}/.zgen/init.zsh ]]; then
  echo "Creating a zgen save"

  # zgen oh-my-zsh
  #
  # # plugins
  # zgen oh-my-zsh plugins/aws
  # # zgen oh-my-zsh plugins/brew
  # zgen oh-my-zsh plugins/docker
  # zgen oh-my-zsh plugins/git
  # # zgen oh-my-zsh plugins/npm
  # # zgen oh-my-zsh plugins/yarn
  # zgen oh-my-zsh plugins/terraform
  # zgen load lukechilds/zsh-nvm
  zgen load mroth/evalcache

  zgen load chrissicool/zsh-256color
  # zgen load supercrabtree/k

  # zgen load zsh-users/zsh-history-substring-search
  # zgen load zsh-users/zsh-autosuggestions

  # completions
  # zgen load srijanshetty/docker-zsh
  # zgen load zsh-users/zsh-completions src
  # zgen load lukechilds/zsh-better-npm-completion

  # theme
  # zgen oh-my-zsh themes/agnoster
  zgen load spaceship-prompt/spaceship-prompt spaceship

  # save all to init script
  zgen save

else
  source ${ZDOTDIR:-${HOME}}/.zgen/init.zsh
fi

# Stash your environment variables in ~/.localrc. This means they'll stay out
# of your main dotfiles repository (which may be public, like this one), but
# you'll have access to them in your scripts.
if [[ -a ~/.localrc ]]
then
  source ~/.localrc
fi

# load the path files
for file in ${(M)config_files:#*/aliases.zsh}
do
  source $file
done

SPACESHIP_BATTERY_SHOW=false
SPACESHIP_TIME_SHOW=true

# place this after nvm initialization!
# autoload -U add-zsh-hook

# load-nvmrc() {
#   [[ -a .nvmrc ]] || return
#   local nvmrc_path
#   nvmrc_path="$(nvm_find_nvmrc)"
#
#   if [ -n "$nvmrc_path" ]; then
#     local nvmrc_node_version
#     nvmrc_node_version=$(nvm version "$(cat "${nvmrc_path}")")
#
#     if [ "$nvmrc_node_version" = "N/A" ]; then
#       nvm install
#     elif [ "$nvmrc_node_version" != "$(nvm version)" ]; then
#       nvm use
#     fi
#   elif [ -n "$(PWD=$OLDPWD nvm_find_nvmrc)" ] && [ "$(nvm version)" != "$(nvm version default)" ]; then
#     echo "Reverting to nvm default version"
#     nvm use default
#   fi
# }
#
# add-zsh-hook chpwd load-nvmrc
# load-nvmrc

# eval "$(zoxide init zsh)"
# eval "$(atuin init zsh)"
_evalcache zoxide init zsh
_evalcache atuin init zsh
# eval 
# zprof
#
# # The next line updates PATH for the Google Cloud SDK.
# if [ -f '/Users/ibartholomew/Downloads/google-cloud-sdk/path.zsh.inc' ]; then . '/Users/ibartholomew/Downloads/google-cloud-sdk/path.zsh.inc'; fi
#
# # The next line enables shell command completion for gcloud.
# if [ -f '/Users/ibartholomew/Downloads/google-cloud-sdk/completion.zsh.inc' ]; then . '/Users/ibartholomew/Downloads/google-cloud-sdk/completion.zsh.inc'; fi
# Set up fzf key bindings and fuzzy completion
source <(fzf --zsh)
export FZF_DEFAULT_COMMAND='fd --type f --strip-cwd-prefix'
