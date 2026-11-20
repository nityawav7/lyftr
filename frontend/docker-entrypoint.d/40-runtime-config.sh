#!/bin/sh
# Writes the API base URL into config.js at container start, so one image can be
# pointed at any backend through the API_BASE_URL environment variable.
set -eu
escaped=$(printf '%s' "${API_BASE_URL:-}" | sed 's/\\/\\\\/g; s/"/\\"/g')
cat > /usr/share/nginx/html/config.js <<JS
window.__APP_CONFIG__ = { API_BASE_URL: "${escaped}" };
JS
echo "runtime config: API_BASE_URL=${API_BASE_URL:-<unset>}"
