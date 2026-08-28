#!/bin/zsh
set -euo pipefail

# Provider credentials remain in the existing 0600 environment file.
source /Users/chanwit/.config/litellm/copilot-auto/litellm.env
: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY is required}"

export COPILOT_PROVIDER_BASE_URL="http://127.0.0.1:4000/v1"
export COPILOT_PROVIDER_TYPE=openai
export COPILOT_PROVIDER_API_KEY="$LITELLM_MASTER_KEY"
export COPILOT_PROVIDER_WIRE_API=responses
export COPILOT_PROVIDER_MODEL_ID=gpt-5.4
export COPILOT_PROVIDER_WIRE_MODEL=auto

# launchd starts KeepAlive jobs concurrently after login/reboot. Wait for the
# LiteLLM upstream before starting the A2A worker, otherwise the worker can
# accept requests while its provider is still unavailable.
_litellm_ready=0
for _attempt in {1..60}; do
  if curl -fsS --max-time 3 \
      -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
      http://127.0.0.1:4000/health/liveliness >/dev/null 2>&1; then
    _litellm_ready=1
    break
  fi
  sleep 2
done
if (( ! _litellm_ready )); then
  print -u2 "LiteLLM did not become ready within 120 seconds; restarting via launchd"
  exit 75
fi

exec /Users/chanwit/WorkSpace/mcp-a2a-bridge/.venv/bin/copilot-a2a-agent --port 9010 --cwd /Users/chanwit/WorkSpace
