#!/usr/bin/env bash
set -Eeuo pipefail

# Refresh Codex auth on the host, then restart only the analyzer.
# The API key is read silently and is never written to disk or logs.

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME_DIR="${CODEX_HOST_HOME:-${HOME}/.codex}"
AUTH_FILE="${CODEX_HOME_DIR}/auth.json"

cd "${ROOT_DIR}"

command -v codex >/dev/null || {
  printf 'ERROR: codex is not installed on the host.\n' >&2
  exit 1
}
command -v docker >/dev/null || {
  printf 'ERROR: docker is not installed or not on PATH.\n' >&2
  exit 1
}

mkdir -p "${CODEX_HOME_DIR}"
if [[ -f "${AUTH_FILE}" ]]; then
  backup="${AUTH_FILE}.backup.$(date -u +%Y%m%dT%H%M%SZ)"
  cp --preserve=mode,timestamps "${AUTH_FILE}" "${backup}"
  chmod 600 "${backup}"
  printf 'Backed up existing Codex auth to %s\n' "${backup}"
fi

printf 'Paste the OpenAI API key (input hidden): '
IFS= read -r -s OPENAI_API_KEY
printf '\n'
if [[ -z "${OPENAI_API_KEY}" ]]; then
  printf 'ERROR: empty API key; nothing changed.\n' >&2
  exit 1
fi

if ! printf '%s\n' "${OPENAI_API_KEY}" | CODEX_HOME="${CODEX_HOME_DIR}" codex login --with-api-key; then
  unset OPENAI_API_KEY
  printf 'ERROR: Codex login failed. The previous auth backup was preserved.\n' >&2
  exit 1
fi
unset OPENAI_API_KEY

chmod 600 "${AUTH_FILE}"
CODEX_HOME="${CODEX_HOME_DIR}" codex login status

# The analyzer receives auth.json through the read-only Compose bind mount.
docker compose restart analyzer

printf '\nWaiting for analyzer health...\n'
for _ in {1..30}; do
  status="$(docker compose ps --format '{{.Service}} {{.Health}} {{.State}}' analyzer 2>/dev/null || true)"
  printf '%s\n' "${status}"
  if [[ "${status}" == *"healthy"* ]]; then
    printf 'Analyzer restarted and is healthy.\n'
    exit 0
  fi
  sleep 2
done

printf 'ERROR: analyzer did not become healthy within 60 seconds.\n' >&2
docker compose logs --tail=80 analyzer >&2 || true
exit 1
