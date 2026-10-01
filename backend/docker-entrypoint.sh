#!/bin/sh
# Container roles: api | worker | migrate | <any command>
set -e
case "$1" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
      --workers "${API_WORKERS:-2}" --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
      --no-server-header
    ;;
  worker)
    exec python -m app.worker
    ;;
  migrate)
    alembic upgrade head
    if [ -d "${REFERENCE_DATA_DIR:-/reference}" ] && [ -n "$(ls -A "${REFERENCE_DATA_DIR:-/reference}" 2>/dev/null)" ]; then
      if [ "${CPT_LICENSE_ACKNOWLEDGED:-false}" = "true" ]; then CPT_FLAG="--i-have-a-cpt-license"; else CPT_FLAG=""; fi
      python -m app.cli kb load-all --data-dir "${REFERENCE_DATA_DIR:-/reference}" $CPT_FLAG
    fi
    python -m app.cli kb bootstrap
    python -m app.cli kb index --system all
    ;;
  *)
    exec "$@"
    ;;
esac
