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

exec /Users/chanwit/WorkSpace/mcp-a2a-bridge/.venv/bin/copilot-a2a-agent --port 9002 --cwd /Users/chanwit/WorkSpace
