# Use `hub` as our git wrapper:
#   http://defunkt.github.com/hub/
hub_path=$(which hub)
if (( $+commands[hub] ))
then
  alias git=$hub_path
fi

# The rest of my fun git aliases
alias gl='git pull --prune'
alias glog="git log --graph --pretty=format:'%Cred%h%Creset %an: %s - %Creset %C(yellow)%d%Creset %Cgreen(%cr)%Creset' --abbrev-commit --date=relative"
alias gp='git push origin HEAD'
alias gd='git diff'
alias gc='git commit'
alias gca='git commit -a'
alias gco='git checkout'
alias gcb='git copy-branch-name'
alias gb='git branch'
alias gs='git status -sb' # upgrade your git if -sb breaks for you. it's fun.
alias gac='git add -A && git commit -m'

alias reload!='. ~/.zshrc'
alias grep='grep --color --exclude-dir={.git,node_modules,Session.vim}'
alias ts='tig status'
alias be='bundle exec'
alias tmux="tmux -2"
alias inv='nvim $(fzf --preview="bat --color=always {}")'

# aws-vault
alias av="aws-vault"
alias ave="aws-vault exec"
alias aveo="aws-vault exec order --"

# dev folders
alias dev="cd ~/Dev"

alias tf="terraform"
# terragrunt
alias tg="terragrunt"
alias tgp="terragrunt plan"
alias tga="terragrunt apply"
alias tgo="terragrunt output"

alias ls="eza --long --icons"
alias exa="eza" # this shouldn't be necessary but something is causing a lingering alias using this
alias cat="bat"
alias psent="policy_sentry"

alias gitroot='cd $(git rev-parse --show-toplevel)'

alias tvg='_tg(){ travelgrunt -out-file ~/.tg-path ${@} && cd "$(cat ~/.tg-path)" }; _tg'
alias t='_tg(){ travelgrunt -out-file ~/.tg-path ${@} && cd "$(cat ~/.tg-path)" }; _tg'
alias tt='_tt(){ travelgrunt -top -out-file ~/.tg-path && cd "$(cat ~/.tg-path)" }; _tt'
