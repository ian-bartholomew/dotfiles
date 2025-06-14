# Performance: Enable profiling if needed
# zmodload zsh/zprof

# Environment variables (fastest first)
export DOTFILES="$HOME/.dotfiles"
export ZSH="$DOTFILES" 
export PROJECTS="$HOME/Dev"
export DEFAULT_USER="$USER"

# Spaceship prompt configuration
export SPACESHIP_BATTERY_SHOW=false
export SPACESHIP_TIME_SHOW=true

# FZF configuration
export FZF_DEFAULT_COMMAND='fd --type f --strip-cwd-prefix'

# Performance: Ultra-fast completion setup
typeset -g zcachedir="$HOME/.zcache"
[[ -d "$zcachedir" ]] || mkdir -p "$zcachedir"

# Load path files immediately (critical for performance)
if [[ -r "$HOME/.config/zsh/path.zsh" ]]; then
  source "$HOME/.config/zsh/path.zsh"
fi

# Ultra-fast completion initialization
autoload -Uz compinit
typeset -g zcompf="$zcachedir/zcompdump"

# Only rebuild completions if older than 24 hours
if [[ $zcompf(#qNmh+24) ]]; then
  compinit -d "$zcompf"
  { zcompile "$zcompf" } &!
else
  # Skip security check for speed
  compinit -C -d "$zcompf"  
fi

# Load core config files efficiently  
typeset -aU config_files
config_files=($HOME/.config/zsh/{env,config,aliases,completion}.zsh(N))

for file in $config_files; do
  [[ -r "$file" ]] && source "$file"
done
unset config_files

# Load prompt immediately (needed for interactive shell)
if [[ ! -s "$HOME/.zgen/init.zsh" ]]; then
  # First-time setup
  [[ ! -d "$HOME/.zgen" ]] && git clone --depth=1 https://github.com/tarjoilija/zgen.git "$HOME/.zgen"
  source "$HOME/.zgen/zgen.zsh"
  
  # Essential plugins
  zgen oh-my-zsh
  zgen oh-my-zsh plugins/git
  zgen load chrissicool/zsh-256color
  zgen load spaceship-prompt/spaceship-prompt spaceship
  zgen load zsh-users/zsh-history-substring-search
  zgen load zsh-users/zsh-autosuggestions
  zgen load zsh-users/zsh-completions src
  
  zgen save
else
  source "$HOME/.zgen/init.zsh"
fi

# Defer heavy operations until after prompt
# {
#   # Load local config
#   [[ -r "$HOME/.localrc" ]] && source "$HOME/.localrc"


#   # NVM lazy loading 
#   if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
#     # Ultra-lazy NVM loading
#     nvm() {
#       unfunction nvm
#       source "$HOME/.nvm/nvm.sh"
#       nvm "$@"
#     }
#     
#     node() {
#       unfunction node
#       source "$HOME/.nvm/nvm.sh"
#       node "$@"
#     }
#     
#     npm() {
#       unfunction npm  
#       source "$HOME/.nvm/nvm.sh"
#       npm "$@"
#     }
#   fi
# } &!

# Initialize tools with caching
eval "$(zoxide init zsh)"
eval "$(atuin init zsh)"

# Set up fzf key bindings and fuzzy completion
source <(fzf --zsh)
export FZF_DEFAULT_COMMAND='fd --type f --strip-cwd-prefix'


# Performance: End profiling if enabled
# zprof
#
#
alias claude="/Users/ibartholomew/.claude/local/claude"

export NVM_DIR="$HOME/.nvm"
[ -s "/opt/homebrew/opt/nvm/nvm.sh" ] && \. "/opt/homebrew/opt/nvm/nvm.sh"  # This loads nvm
[ -s "/opt/homebrew/opt/nvm/etc/bash_completion.d/nvm" ] && \. "/opt/homebrew/opt/nvm/etc/bash_completion.d/nvm"  # This loads nvm bash_completion
export GPG_TTY=$(tty)
