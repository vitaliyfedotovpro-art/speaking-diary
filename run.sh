#!/bin/bash
# Запуск дневника. Ключи: Gemini (разговор, память, голос) и Groq (распознавание речи).
cd "$(dirname "$0")"
[ -z "$GEMINI_API_KEY" ] && [ -f "$HOME/.diary/key" ] && export GEMINI_API_KEY="$(tr -d '[:space:]' < "$HOME/.diary/key")"
[ -z "$GROQ_API_KEY" ] && [ -f "$HOME/.diary/groq_key" ] && export GROQ_API_KEY="$(tr -d '[:space:]' < "$HOME/.diary/groq_key")"
exec python3 -m diary.server "${1:-8791}"
