#!/bin/bash
# Log token usage to file
# Usage: ./log_tokens.sh "<session_info>"

SESSION_INFO="$1"
DATE=$(date -u +%Y-%m-%d)
TIME=$(date -u +%H:%M:%S)
LOG_FILE="/root/.openclaw/workspace/memory/token-usage.md"

echo "$DATE $TIME - $SESSION_INFO" >> "$LOG_FILE"
