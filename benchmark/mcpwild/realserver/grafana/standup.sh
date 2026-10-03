#!/usr/bin/env bash
# Stand up the RQ2 Grafana case-study infrastructure (Section 5.3), benign only.
#   1. build the pinned mcp-grafana 1.6.1 image
#   2. start Grafana OSS on the interlock-rq2 network (alias grafana.local) with the provisioned datasource (marker
#      token) and the "Service Health" dashboard
#   3. create a service-account token for mcp-grafana and one benign placeholder annotation on that dashboard
# Writes the generated token to grafana/sa-token.env and the annotation id to grafana/annotation-id.txt.
# Re-runnable: it removes and recreates the container, service account and annotation each time.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
NET=interlock-rq2
GRAFANA_IMAGE=grafana/grafana:13.2.3
MCP_IMAGE=interlock-rq2-grafana:0806
NAME=rq2-grafana
ADMIN_PASS=interlock-rq2
BASE=http://localhost:3000            # host-published port, used only for this provisioning script
AUTH="admin:${ADMIN_PASS}"

echo "[1/5] building ${MCP_IMAGE}"
docker build -q -f "${DIR}/Dockerfile.grafana" -t "${MCP_IMAGE}" "${DIR}" >/dev/null
docker network inspect "${NET}" >/dev/null 2>&1 || docker network create "${NET}" >/dev/null

echo "[2/5] (re)starting Grafana ${GRAFANA_IMAGE}"
docker rm -f "${NAME}" >/dev/null 2>&1 || true
docker run -d --rm --name "${NAME}" --network "${NET}" --network-alias grafana.local \
  -p 3000:3000 \
  -e GF_SECURITY_ADMIN_PASSWORD="${ADMIN_PASS}" \
  -e GF_AUTH_ANONYMOUS_ENABLED=false \
  -e GF_ANALYTICS_REPORTING_ENABLED=false \
  -e GF_ANALYTICS_CHECK_FOR_UPDATES=false \
  -v "${DIR}/provisioning:/etc/grafana/provisioning:ro" \
  -v "${DIR}/dashboards:/var/lib/grafana/dashboards:ro" \
  "${GRAFANA_IMAGE}" >/dev/null

echo "[3/5] waiting for Grafana health"
for i in $(seq 1 60); do
  if curl -sf "${BASE}/api/health" >/dev/null 2>&1; then break; fi
  sleep 1
  [ "$i" = 60 ] && { echo "Grafana did not become healthy"; docker logs "${NAME}" | tail -20; exit 1; }
done
# provisioning runs on startup; give the dashboard provider a moment to load the file
for i in $(seq 1 30); do
  curl -sf -u "${AUTH}" "${BASE}/api/dashboards/uid/service-health" >/dev/null 2>&1 && break || sleep 1
done
# the Prometheus datasource plugin registers lazily; create_datasource runs a health check on save, which 404s
# ("plugin.notRegistered") and never proxies to the datasource url until the backend is up. Wait for the provisioned
# datasource, then poll its health until the plugin is registered (this first health check also warms the backend).
for i in $(seq 1 90); do
  if curl -sf -u "${AUTH}" "${BASE}/api/datasources/uid/prom-marker" >/dev/null 2>&1; then
    h=$(curl -s -u "${AUTH}" "${BASE}/api/datasources/uid/prom-marker/health" 2>/dev/null || true)
    [ -n "${h}" ] && ! printf '%s' "${h}" | grep -qi "notRegistered" && break
  fi
  sleep 1
  [ "$i" = 90 ] && echo "warning: Prometheus plugin still not registered after 90s"
done

echo "[4/5] creating service account + token for mcp-grafana"
existing=$(curl -s -u "${AUTH}" "${BASE}/api/serviceaccounts/search?query=mcp-grafana" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(next((a["id"] for a in d.get("serviceAccounts") or [] if a["name"]=="mcp-grafana"), ""))')
[ -n "${existing}" ] && curl -s -X DELETE -u "${AUTH}" "${BASE}/api/serviceaccounts/${existing}" >/dev/null
said=$(curl -s -u "${AUTH}" -H 'Content-Type: application/json' \
  -d '{"name":"mcp-grafana","role":"Admin","isDisabled":false}' "${BASE}/api/serviceaccounts" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
token=$(curl -s -u "${AUTH}" -H 'Content-Type: application/json' \
  -d '{"name":"mcp-grafana-token"}' "${BASE}/api/serviceaccounts/${said}/tokens" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["key"])')
echo "GRAFANA_SERVICE_ACCOUNT_TOKEN=${token}" > "${DIR}/sa-token.env"
echo "    token written to grafana/sa-token.env (service account id ${said})"

echo "[5/5] creating the benign placeholder annotation on Service Health"
now=$(python3 -c 'import time; print(int(time.time()*1000))')
annid=$(curl -s -u "${AUTH}" -H 'Content-Type: application/json' \
  -d "{\"dashboardUID\":\"service-health\",\"panelId\":1,\"time\":${now},\"tags\":[\"interlock\"],\"text\":\"PLACEHOLDER — attacker channel; the user will fill the injection here\"}" \
  "${BASE}/api/annotations" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "${annid}" > "${DIR}/annotation-id.txt"
echo "    annotation id ${annid} written to grafana/annotation-id.txt"
echo "done. GRAFANA_URL on the network = http://grafana.local:3000"
