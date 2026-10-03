#!/usr/bin/env bash
#
# scrape-n-search — interactive setup wizard.
# Lets a new user pick which provider to configure, tests each API key live,
# and finishes by setting the default provider (and fallback priority when
# more than one provider is configured). Writes everything to mcp-server/.env.
#
# Everything above the "STAGES" marker is the wizard library: do not hand-edit
# it. Author the per-step stages below the marker.

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────
# Wizard library — delightful, consistent UX. Identical across every wizard.
# ──────────────────────────────────────────────────────────────────────────

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
  BLUE=$(tput setaf 4); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3); RED=$(tput setaf 1)
else
  BOLD=""; DIM=""; RESET=""; BLUE=""; GREEN=""; YELLOW=""; RED=""
fi

# Author sets this at the top of the stages section.
TOTAL_STAGES=0

_STAGE_INDEX=0
ENV_FILE="${ENV_FILE:-.env}"
WRITTEN_ENV=()    # KEYs written to ENV_FILE this run
WRITTEN_SECRET=() # secret NAMEs set this run
SKIPPED=()        # things we couldn't do (e.g. gh missing)

# _clear — wipe the terminal so only the current step is on screen. No-op when
# output isn't a terminal, so piped logs stay readable.
_clear() {
  [[ -t 1 ]] || return 0
  if command -v tput >/dev/null 2>&1; then tput clear; else printf '\033[2J\033[3J\033[H'; fi
}

# banner "Title" — opening frame: what this wizard does.
banner() {
  _clear
  printf '\n%s%s  %s%s\n' "$BOLD" "$BLUE" "$1" "$RESET"
  printf '%s  %s stages%s\n\n' "$DIM" "$TOTAL_STAGES" "$RESET"
  printf '%s  You drive the browser; this wizard tells you exactly what to do and\n' "$DIM"
  printf '  captures the values you copy back. Stop any time with Ctrl-C and re-run\n'
  printf '  later — it remembers values already saved.%s\n' "$RESET"
  pause "Ready to start?"
}

# stage "Name" — clear the screen, then announce a stage and show progress.
# Clearing keeps only the current step on screen.
stage() {
  _clear
  _STAGE_INDEX=$((_STAGE_INDEX + 1))
  printf '\n%s%s▸ Stage %s/%s · %s%s\n' \
    "$BOLD" "$BLUE" "$_STAGE_INDEX" "$TOTAL_STAGES" "$1" "$RESET"
}

# say "..." — a plain instruction line.
say()  { printf '  %s\n' "$1"; }
# step "..." — a numbered-feeling action the human takes in the browser.
step() { printf '  %s•%s %s\n' "$BLUE" "$RESET" "$1"; }
note() { printf '  %s%s%s\n' "$DIM" "$1" "$RESET"; }
warn() { printf '  %s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }

# open_url URL — open in the human's browser, cross-platform incl. WSL.
open_url() {
  local url="$1"
  printf '  %s↗ opening%s %s\n' "$GREEN" "$RESET" "$url"
  { if   command -v wslview     >/dev/null 2>&1; then wslview "$url"
    elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$url"
    elif command -v xdg-open    >/dev/null 2>&1; then xdg-open "$url"
    elif command -v open        >/dev/null 2>&1; then open "$url"
    else warn "couldn't open a browser — visit it manually: $url"; fi
  } >/dev/null 2>&1 || warn "couldn't open a browser — visit it manually: $url"
}

# pause "msg" — wait for the human to confirm they've done the manual part.
pause() {
  printf '  %s%s%s ' "$DIM" "${1:-Press Enter to continue}" "$RESET"
  read -r _ || true
}

# confirm "question" — y/N gate; returns success on yes.
confirm() {
  local reply=""
  printf '  %s? %s [y/N] ' "$YELLOW" "$1"
  read -r reply || true
  [[ "$reply" =~ ^[Yy] ]]
}

# _existing KEY — current value of KEY in ENV_FILE, if any.
_existing() {
  [[ -f "$ENV_FILE" ]] || return 1
  local line; line=$(grep -E "^${1}=" "$ENV_FILE" | tail -n1) || return 1
  printf '%s' "${line#*=}"
}

# ask KEY "Prompt" — read a value into $KEY. Offers the existing .env value as
# a default on re-runs (Enter keeps it). Visible input (non-secret).
ask() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -r input || true
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

# ask_secret KEY "Prompt" — like ask, but input is hidden.
ask_secret() {
  local key="$1" prompt="$2" current input
  current=$(_existing "$key" || true)
  if [[ -n "$current" ]]; then
    printf '  %s%s%s %s[Enter keeps current]%s ' "$BOLD" "$prompt" "$RESET" "$DIM" "$RESET"
  else
    printf '  %s%s%s ' "$BOLD" "$prompt" "$RESET"
  fi
  read -rs input || true
  printf '\n'
  [[ -z "$input" && -n "$current" ]] && input="$current"
  printf -v "$key" '%s' "$input"
}

# write_env KEY VALUE — upsert KEY=VALUE into ENV_FILE (creates it; replaces
# any existing line). Idempotent.
write_env() {
  local key="$1" value="$2" tmp
  touch "$ENV_FILE"
  tmp=$(mktemp)
  grep -vE "^${key}=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  WRITTEN_ENV+=("$key")
  printf '  %s✓ wrote%s %s → %s\n' "$GREEN" "$RESET" "$key" "$ENV_FILE"
}

# set_secret NAME VALUE — set a GitHub Actions repo secret via gh. Falls back
# to a warning (and records it) if gh is unavailable or unauthenticated.
set_secret() {
  local name="$1" value="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if printf '%s' "$value" | gh secret set "$name" >/dev/null 2>&1; then
      WRITTEN_SECRET+=("$name")
      printf '  %s✓ set%s GitHub secret %s\n' "$GREEN" "$RESET" "$name"
      return
    fi
  fi
  SKIPPED+=("GitHub secret $name (set it manually: gh secret set $name)")
  warn "skipped GitHub secret $name — gh not ready; set it later"
}

# set_var NAME VALUE — set a GitHub Actions repo variable (non-secret).
set_var() {
  local name="$1" value="$2"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    if gh variable set "$name" --body "$value" >/dev/null 2>&1; then
      printf '  %s✓ set%s GitHub variable %s\n' "$GREEN" "$RESET" "$name"
      return
    fi
  fi
  SKIPPED+=("GitHub variable $name")
  warn "skipped GitHub variable $name — gh not ready; set it later"
}

# finish — clear, then a closing summary of everything configured.
finish() {
  _clear
  printf '\n%s%s  ✓ Setup complete%s\n' "$BOLD" "$GREEN" "$RESET"
  (( ${#WRITTEN_ENV[@]} ))    && note "wrote ${#WRITTEN_ENV[@]} value(s) to $ENV_FILE: ${WRITTEN_ENV[*]}"
  (( ${#WRITTEN_SECRET[@]} )) && note "set ${#WRITTEN_SECRET[@]} GitHub secret(s): ${WRITTEN_SECRET[*]}"
  if (( ${#SKIPPED[@]} )); then
    printf '\n'; warn "still to do by hand:"
    for s in "${SKIPPED[@]}"; do note "  - $s"; done
  fi
  printf '\n'
}

# ──────────────────────────────────────────────────────────────────────────
# STAGES — author this section. One stage() per step the human takes.
# Replace the example below. Set TOTAL_STAGES to match the stages you write.
# ──────────────────────────────────────────────────────────────────────────

# Write config into the repo's mcp-server/.env, wherever the script is run from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/mcp-server/.env"
PY="$(command -v python3 || command -v python || true)"

# Provider registry (ids must match the router's provider names).
PROVIDER_IDS=(tinyfish tavily exa zenrows scrappey firecrawl serper jina)
PROVIDER_LABELS=(TinyFish Tavily Exa ZenRows "Scrappey (recommended)" Firecrawl Serper Jina)
PROVIDER_KEYS=(TINYFISH_API_KEY TAVILY_API_KEY EXA_API_KEY ZENROWS_API_KEY SCRAPPEY_API_KEY FIRECRAWL_API_KEY SERPER_API_KEY JINA_API_KEY)
PROVIDER_URLS=(https://tinyfish.ai https://app.tavily.com https://dashboard.exa.ai https://app.zenrows.com https://app.scrappey.com https://www.firecrawl.dev https://serper.dev https://jina.ai)
PROVIDER_NOTES=(
  "Free search + fetch, no card. Real full-browser renders -> clean markdown."
  "1,000 free credits/mo, no card. Agent-native search + content extraction."
  "Semantic search. \$20 free credits, pure pay-as-you-go, no subscription."
  "5,000 free credits/mo, no card. Credit-based scraping, anti-bot handling."
  "Recommended: cheapest full-browser (EUR 1/1K), pay-as-you-go, balance never expires."
  "1,000 free credits/mo, no card. Search + scrape; the only crawl/extract provider."
  "2,500 free queries, no card. Raw Google SERP JSON (search only)."
  "Optional key - fetch works WITHOUT one (keyless fallback); key enables s.jina.ai search."
)

QUIT=0
PRIORITIZED=0  # 1 once the user has (re)set provider priority this run

# is_configured <idx> — true if the provider already has a key in .env.
is_configured() {
  local keyname="${PROVIDER_KEYS[$1]}"
  [[ -n "$(_existing "$keyname" || true)" ]]
}

# join_config — comma-joined list of currently configured providers.
join_config() {
  local IFS=','; printf '%s' "${ENABLED_PROVIDERS[*]}"
}

# configured_count — number of providers with a key in .env.
configured_count() {
  local i n=0
  for i in "${!PROVIDER_IDS[@]}"; do
    if is_configured "$i"; then n=$((n + 1)); fi
  done
  printf '%s' "$n"
}

# refill_enabled — (re)build ENABLED_PROVIDERS from the keys currently in .env.
refill_enabled() {
  local i
  ENABLED_PROVIDERS=()
  for i in "${!PROVIDER_IDS[@]}"; do
    if is_configured "$i"; then
      ENABLED_PROVIDERS+=("${PROVIDER_IDS[$i]}")
    fi
  done
}

# prioritize — prompt for the default + fallback order among configured
# providers and write PROVIDER_PRIORITY / DEFAULT_PROVIDER. A single configured
# provider is auto-set. Returns 0 on success.
prioritize() {
  local ok tok first
  refill_enabled
  _clear
  printf '\n%s%s  Provider order — default & fallback%s\n' "$BOLD" "$BLUE" "$RESET"
  if (( ${#ENABLED_PROVIDERS[@]} < 2 )); then
    if (( ${#ENABLED_PROVIDERS[@]} == 1 )); then
      first="${ENABLED_PROVIDERS[0]}"
      write_env DEFAULT_PROVIDER "$first"
      write_env PROVIDER_PRIORITY "$first"
      say "Single provider ${first} — set as the default."
    fi
    return 0
  fi
  say "Configured: ${ENABLED_PROVIDERS[*]}"
  say "Highest priority first; the first becomes DEFAULT_PROVIDER, the rest are"
  say "the fallback order tried on error."
  note "Suggested (Enter accepts): $(join_config)"
  ask PROVIDER_PRIORITY "Priority order (first = default):"
  if [[ -z "$PROVIDER_PRIORITY" ]]; then
    PROVIDER_PRIORITY="$(join_config)"
  fi
  ok=1
  for tok in ${PROVIDER_PRIORITY//,/ }; do
    printf '%s\n' "${PROVIDER_IDS[@]}" | grep -qx "$tok" || { warn "unknown provider: '$tok'"; ok=0; }
  done
  if (( ok )); then
    write_env PROVIDER_PRIORITY "$PROVIDER_PRIORITY"
    first="${PROVIDER_PRIORITY%%,*}"
    write_env DEFAULT_PROVIDER "$first"
    printf '%s\n' "${ENABLED_PROVIDERS[@]}" | grep -qx "$first" || \
      warn "DEFAULT_PROVIDER='$first' has no API key — the server will not start."
    return 0
  fi
  warn "Not writing PROVIDER_PRIORITY — remove unknown provider names and retry."
  return 1
}

# verify_key <idx> <key> — probe the provider API with a minimal real call.
verify_key() {
  local idx="$1" key="$2" out
  if [[ -z "$PY" ]]; then
    warn "python3 not found — key saved WITHOUT a live test."
    return 0
  fi
  say "Testing your key against ${PROVIDER_LABELS[$idx]}... (one call, cheap)"
  if ! out="$("$PY" "$SCRIPT_DIR/scripts/check_keys.py" "${PROVIDER_IDS[$idx]}" "$key" 2>&1)"; then
    printf '  %s%s%s\n' "$RED" "$(printf '%s' "$out" | tail -n1)" "$RESET"
    return 1
  fi
  printf '  %s✓ %s%s\n' "$GREEN" "$out" "$RESET"
  return 0
}

# configure_provider <idx> — (optionally confirm overwrite, then) prompt for the
# key, verify it, and on success decide whether to keep going. Returns with
# QUIT possibly set.
configure_provider() {
  local idx="$1" keyname="${PROVIDER_KEYS[$1]}" keyval oldval
  if is_configured "$idx"; then
    _clear
    printf '\n%s%s▸ %s — API key%s\n' "$BOLD" "$BLUE" "${PROVIDER_LABELS[$idx]}" "$RESET"
    say "${PROVIDER_NOTES[$idx]}"
    warn "${PROVIDER_LABELS[$idx]%% *} already has a key configured."
    confirm "Overwrite the existing key" || {
      note "Kept the existing key — nothing changed."
      pause
      return 0
    }
  fi
  while :; do
    _clear
    printf '\n%s%s▸ %s — API key%s\n' "$BOLD" "$BLUE" "${PROVIDER_LABELS[$idx]}" "$RESET"
    say "${PROVIDER_NOTES[$idx]}"
    open_url "${PROVIDER_URLS[$idx]}"
    step "Copy your API key from your dashboard."
    if is_configured "$idx"; then
      say "Enter a new key below to replace the existing one."
    fi
    ask_secret "$keyname" "Paste your ${PROVIDER_LABELS[$idx]%% *} API key (Enter to skip):"
    keyval="${!keyname}"
    if [[ -z "$keyval" ]]; then
      note "Skipped — nothing changed for this provider."
      return 0
    fi
    oldval="$(_existing "$keyname" || true)"
    if [[ -n "$oldval" && "$keyval" == "$oldval" ]]; then
      say "Key unchanged — existing key kept, no test needed."
      return 0
    fi
    if verify_key "$idx" "$keyval"; then
      write_env "$keyname" "$keyval"
      say "Your key works."
      if (( $(configured_count) >= 2 )); then
        printf '  %sRe-prioritize providers now? %s [y/N] ' "$YELLOW" "$RESET"
        read -r REPLY || REPLY=
        if [[ "${REPLY,,}" == "y" ]]; then
          if prioritize; then PRIORITIZED=1; fi
        fi
      fi
      printf '  %sConfigure another provider? %s [y/N] ' "$YELLOW" "$RESET"
      read -r REPLY || REPLY=
      [[ "${REPLY,,}" == "y" ]] && return 0
      QUIT=1; return 0
    else
      printf '  %sThat key was rejected.%s\n' "$RED" "$RESET"
      printf '  %s[R]etry this key / [A]lternate provider / [Q]uit setup%sa: ' "$BOLD" "$RESET"
      read -r REPLY || { QUIT=1; return 0; }
      case "${REPLY,,}" in
        a) return 0 ;;                 # back to the menu, nothing written
        q) QUIT=1; return 0 ;;
        *) continue ;;                 # retry this provider's key
      esac
    fi
  done
}

TOTAL_STAGES=1
banner "scrape-n-search — provider setup"

# ── interactive loop: pick a provider, add + verify its key ───────────────
while [[ "$QUIT" == 0 ]]; do
  _clear
  printf '\n%s%s  Configure providers%s\n\n' "$BOLD" "$BLUE" "$RESET"
  for i in "${!PROVIDER_IDS[@]}"; do
    if is_configured "$i"; then
      printf '  %s%2d)%s %s %s[configured]%s\n' "$BOLD" "$((i + 1))" "$RESET" "${PROVIDER_LABELS[$i]}" "$GREEN" "$RESET"
    else
      printf '  %s%2d)%s %s\n' "$BOLD" "$((i + 1))" "$RESET" "${PROVIDER_LABELS[$i]}"
    fi
  done
  printf '  %s%2d)%s Done — finish setup\n\n' "$BOLD" "0" "$RESET"
  printf '  %sChoose a provider (or 0 to finish): %s' "$BOLD" "$RESET"
  if ! read -r choice; then break; fi
  case "$choice" in
    0|q|Q) break ;;
    '') continue ;;
    [1-9])
      idx=$((choice - 1))
      if (( idx < ${#PROVIDER_IDS[@]} )); then
        configure_provider "$idx"
      else
        warn "no such provider: $choice"
      fi
      ;;
    *) warn "invalid choice: $choice" ;;
  esac
done

# ── finish: default provider + fallback priority from what is configured ──
_clear
printf '\n%s%s  Finishing up%s\n' "$BOLD" "$BLUE" "$RESET"
case $(configured_count) in
  0)
    warn "No providers are configured."
    say "Re-run this wizard and add at least one API key, then the server will work."
    ;;
  *)
    if (( PRIORITIZED )); then
      refill_enabled
      note "Provider priority was already set earlier in this run."
    else
      prioritize
    fi
    ;;
esac

finish
printf '\n  %sRun the server:%s  .venv/bin/scrape-n-search\n\n' "$BOLD" "$RESET"
