#!/bin/sh
set -e

# Create superuser on first run if not already existing
/pb/pocketbase superuser create admin@example.com Admin12345! 2>/dev/null || true

# Start the server
exec /pb/pocketbase serve --http=0.0.0.0:8090
