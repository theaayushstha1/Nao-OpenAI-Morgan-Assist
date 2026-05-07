# NAO Voice Pipeline — Grafana dashboard

Single dashboard for Phase 9 of the architecture rework. Visualizes everything `server/metrics.py` exposes: per-phase latency, turn outcomes, wake gates, crisis blocks, camera consent flips, gesture intents, brain-sync pushes, and CS Navigator + Vision tool latency.

Designed for **Grafana 11+** against a **Prometheus** datasource that scrapes the FastAPI server at `http://localhost:5050/metrics`.

## Files

- `grafana_voice.json` — dashboard, 10 panels. The `__inputs` block defines a single placeholder `${DS_PROMETHEUS}` so the user picks a Prometheus datasource on import.
- `README.md` — this file.

## Panels at a glance

| # | Panel | Type | Source metric | PromQL |
|---|-------|------|---------------|--------|
| 1 | Per-phase latency (p50 / p95) | timeseries | `nao_phase_latency_ms` | `histogram_quantile(0.5\|0.95, sum by (le, phase) (rate(nao_phase_latency_ms_bucket[5m])))` |
| 2 | Turns / minute by outcome | timeseries (stacked bars) | `nao_turns_total` | `sum by (outcome) (rate(nao_turns_total[1m])) * 60` |
| 3 | Wake events / minute by gate | timeseries (stepped) | `nao_wake_events_total` | `sum by (gate) (rate(nao_wake_events_total[1m])) * 60` |
| 4 | Crisis blocks (cumulative) | stat | `nao_crisis_blocks_total` | `sum(nao_crisis_blocks_total)` |
| 5 | Echo cooldown drops / minute | timeseries | `nao_motion_short_circuits_total` | `sum(rate(nao_motion_short_circuits_total[1m])) * 60` |
| 6 | Camera state changes (timeline) | timeseries (stacked bars) | `nao_camera_state_changes_total` | `sum by (new_state) (rate(nao_camera_state_changes_total[1m])) * 60` |
| 7 | Gesture intents (last hour) | barchart | `nao_gesture_calls_total` | `sum by (intent) (increase(nao_gesture_calls_total[1h]))` |
| 8 | Brain-sync pushes | timeseries | `nao_brain_sync_pushes_total` | `sum by (direction) (rate(nao_brain_sync_pushes_total[1m])) * 60` |
| 9 | CS Navigator call latency | timeseries | `nao_phase_latency_ms{phase="cs_navigator_call"}` | `histogram_quantile(p, sum by (le) (rate(nao_phase_latency_ms_bucket{phase="cs_navigator_call"}[5m])))` |
| 10 | Vision call latency | timeseries | `nao_phase_latency_ms{phase="vision_call"}` | `histogram_quantile(p, sum by (le) (rate(nao_phase_latency_ms_bucket{phase="vision_call"}[5m])))` |

> **Note on metric names:** the Prometheus metric the histogram exports is `nao_phase_latency_ms` (defined in `server/metrics.py`), so the bucket series is `nao_phase_latency_ms_bucket`. The Phase 9 task map references `latency_ms_bucket` as shorthand — the dashboard uses the real fully-qualified name so queries actually match the `/metrics` output.

## Run Prometheus + Grafana locally (docker-compose)

Drop the following into a fresh directory (anywhere on your laptop). It runs Prometheus + Grafana, points Prometheus at `host.docker.internal:5050/metrics` (the FastAPI server running on your Mac), and pre-provisions a Prometheus datasource in Grafana so import is one click.

### `docker-compose.yml`

```yaml
services:
  prometheus:
    image: prom/prometheus:v2.54.1
    container_name: nao-prom
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    extra_hosts:
      # macOS Docker Desktop maps host.docker.internal automatically;
      # Linux users need this line so the container can reach the host.
      - "host.docker.internal:host-gateway"

  grafana:
    image: grafana/grafana:11.2.0
    container_name: nao-grafana
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_AUTH_ANONYMOUS_ORG_ROLE: Viewer
    volumes:
      - ./grafana-datasource.yml:/etc/grafana/provisioning/datasources/prometheus.yml:ro
    depends_on:
      - prometheus
```

### `prometheus.yml`

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

scrape_configs:
  - job_name: nao-server
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:5050"]
```

### `grafana-datasource.yml`

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    uid: prometheus
    editable: true
```

### Start everything

```bash
# Terminal 1: run the FastAPI server (it must expose /metrics on :5050).
cd server && uvicorn server:app --port 5050 --reload

# Terminal 2: bring up Prom + Grafana.
docker compose up -d

# Open http://localhost:3000 (anonymous Viewer is enabled, or log in admin/admin)
```

## Import the dashboard

1. Browse to `http://localhost:3000/dashboard/import`.
2. Click **Upload JSON file** and pick `server/dashboards/grafana_voice.json`.
3. When Grafana prompts for the `DS_PROMETHEUS` input, select the auto-provisioned **Prometheus** datasource (`uid: prometheus`).
4. Click **Import**.

The dashboard `uid` is `nao-voice-v2`; you can deep-link to it at `http://localhost:3000/d/nao-voice-v2`.

### CLI alternative (Grafana HTTP API)

If you have a service-account token (or use admin/admin):

```bash
DS_UID=$(curl -s -u admin:admin http://localhost:3000/api/datasources/name/Prometheus | jq -r .uid)

jq --arg uid "$DS_UID" '
  .__inputs |= [] |                           # consume input
  ( .. | select(.uid? == "${DS_PROMETHEUS}").uid ) |= $uid
' server/dashboards/grafana_voice.json > /tmp/dashboard_resolved.json

curl -X POST -u admin:admin \
  -H "Content-Type: application/json" \
  -d "$(jq '{dashboard: ., overwrite: true, folderUid: ""}' /tmp/dashboard_resolved.json)" \
  http://localhost:3000/api/dashboards/db
```

## Example alert rule (Prometheus)

Drop this in `prometheus.yml` (under `rule_files`) or in a Grafana managed alert. The Phase 9 task map called out crisis blocks as the obvious "alert if > 0" panel; this rule fires the moment any `nao_crisis_blocks_total` increment is observed.

### `alerts.yml`

```yaml
groups:
  - name: nao-voice
    interval: 15s
    rules:
      - alert: NaoCrisisBlock
        expr: increase(nao_crisis_blocks_total[1m]) > 0
        for: 0m
        labels:
          severity: critical
          service: nao-voice
        annotations:
          summary: "NAO crisis-gate fired ({{ $value }} blocks in last 1m)"
          description: |
            safety.crisis_check rejected at least one user turn before agent
            dispatch. The user almost certainly heard the 988-hotline reply.
            Spot-check the conversation log and confirm a human follow-up.
          runbook_url: https://github.com/your-org/nao/blob/main/docs/runbooks/crisis-gate.md
```

To wire it in:

```yaml
# prometheus.yml
rule_files:
  - alerts.yml
```

A parallel set of alert candidates (not enabled by default; tune SLO before flipping on):

| Alert | Expression | Threshold |
|-------|------------|-----------|
| Slow first audio | `histogram_quantile(0.95, sum by (le) (rate(nao_phase_latency_ms_bucket{phase="e2e_user_to_first_audio"}[5m]))) > 2500` | > 2.5s p95 for 5m |
| High echo drops | `sum(rate(nao_motion_short_circuits_total[5m])) * 60 > 10` | > 10/min for 10m |
| Vision stalled | `histogram_quantile(0.95, sum by (le) (rate(nao_phase_latency_ms_bucket{phase="vision_call"}[5m]))) > 5000` | > 5s p95 for 5m |

## Updating the dashboard

This dashboard is **dashboard-as-code**: edit JSON in this directory and re-import (or run the curl one-liner). Grafana lets you make UI tweaks too — when you do, **export with the "Export for sharing externally" option enabled** so the JSON keeps the `${DS_PROMETHEUS}` placeholder. Otherwise it bakes in the local datasource UID and breaks for the next person.
