#!/bin/sh
# Detached reset driver. The Stop hook's timestamp proves this turn finished.
set -u
terminal=$1 pane=$2 target=$3 sid=$4 marker=$5 handoff=$6 lang=$7
state_dir=${KEEL_HOME:-$HOME}/.keel/state/auto-handoff
stopped="$state_dir/$sid.stopped"
before=$(cat "$stopped" 2>/dev/null || printf '')
ready=0
i=0
while [ "$i" -lt 120 ]; do
  if [ -f "$stopped" ]; then
    at=$(cat "$stopped" 2>/dev/null || printf 0)
    if [ "$at" != "$before" ]; then ready=1; break; fi
  fi
  i=$((i + 1))
  sleep 2
done
[ "$ready" -eq 1 ] || exit 1
sleep 2
if [ "$target" = claude ]; then reset=/clear; else reset=/new; fi
if [ "$terminal" = herdr ]; then
  command -v herdr >/dev/null 2>&1 || exit 1
  herdr pane send-text "$pane" "$reset" || exit 1
  herdr pane send-keys "$pane" Enter || exit 1
else
  command -v tmux >/dev/null 2>&1 || exit 1
  tmux send-keys -t "$pane" -l "$reset" || exit 1
  tmux send-keys -t "$pane" Enter || exit 1
fi
i=0
while [ "$i" -lt 30 ]; do
  [ -f "$marker.claimed" ] && break
  i=$((i + 1))
  sleep 2
done
[ -f "$marker.claimed" ] || exit 1
if [ "$lang" = ko ]; then
  prompt="$handoff 파일을 읽고 handoff 스킬의 복원 동작을 실행하세요. 묻지 말고 남은 작업을 이어가세요."
else
  prompt="Read $handoff and use the handoff skill's restore action. Continue the unfinished work without asking."
fi
if [ "$terminal" = herdr ]; then
  herdr pane send-text "$pane" "$prompt" || exit 1
  herdr pane send-keys "$pane" Enter || exit 1
else
  tmux send-keys -t "$pane" -l "$prompt" || exit 1
  tmux send-keys -t "$pane" Enter || exit 1
fi
