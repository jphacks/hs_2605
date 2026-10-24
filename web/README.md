# IBUKI Web — DEMO / LIVE

React + TypeScript + Vite + Recharts. Default mode is DEMO. LIVE receives statuses from `ws://localhost:8000/ws`; this repository change does **not** provide a Python gateway. No phone notifications, diagnostic rules, infant alert thresholds, or SIDS prevention capability are implemented.

## Local use

Requires Node.js 20.19+ or 22.12+.

```sh
cd web
npm install
npm run dev
```

Open Vite's printed URL (normally http://127.0.0.1:5173).

```sh
npm test
npm run build
npm run preview
```

DEMO retains five manually selectable states. LIVE has no scenario controls and never falls back to mock data. Switching modes resets readings/history/events and closes the prior subscription. LIVE denotes server receipt, **not verified sensor provenance**: the existing protocol cannot distinguish real sensor data from recordings or simulated server data. Japanese labels explicitly call these received values.

## Data boundary

- `src/data/status.ts`: unchanged dashboard-local mirror of `BrainStatus` / `StatusSource`. The shared protocol is untouched.
- `src/data/mockSource.ts`: unchanged demo source: 20 Hz, four points every 200 ms.
- `src/data/webSocketSource.ts`: a `StatusSource` implementation with runtime validation, separate alert callback, reconnection and cleanup.
- `src/data/waveform.ts` / `src/useDashboard.ts`: bounded 30-second / 4096-point history, configurable or estimated timing.
- `src/App.tsx`: Japanese mode controls, connection indicator, event display and dashboard.
- `tests/sources.test.mjs`: deterministic fake-socket/timer tests using Node's test runner; no test server or extra dependencies.

Expected status (unchanged):

```json
{"t":12345,"disp_mm":[0.81,0.95,1.02,1.07],"bpm":16.2,"motion":120,"present":true,"state":"OK"}
```

`bpm` can be `null`. States are EMPTY, OK, STILL, CHECK and ALERT. LIVE displays `motion` as a raw received index and does not apply the demo's illustrative movement cutoff. Demo BPM 16.2 is not an infant reference range.

Alert events are routed separately (never interpreted as status):

```json
{"type":"alert","reason":"no_breath","since_s":10,"t":23456}
```

For events, `t` is optional. The most recent event is shown as an event record, not a replacement for current Brain state; it resets on reconnection/mode change. Neither mode sends commands, notification requests, or phone alerts.

## Connection behavior

- Connecting → socket open / connected → valid statuses displayed.
- Socket closed/error → disconnected; retry after 1, 2, 4, 8, then at most 10 seconds. A valid status resets the backoff.
- Open socket with no valid status for 3 seconds → stale. The deadline starts at socket open and resets only on valid statuses, not malformed traffic or alert events. This is transport freshness, **not a medical timeout**.
- Disconnected/stale: readings and waveform are hidden. Valid status receipt restores display. Timestamp gaps >3 seconds or a backward/repeated timestamp reset waveform history to avoid misleading connections.
- Invalid JSON, non-text frames, invalid status/event fields and unknown messages are ignored. Status arrays are capped at 4096 points and text frames at 1 MB as resource bounds, not physiological thresholds.
- Unsubscribe closes the socket and clears retries/stale timers; late callbacks are ignored, including under React StrictMode.

## Person 2 integration agreement — still required

1. **Timestamp unit is ambiguous.** This client follows the data-spec/current-web convention of `t` in milliseconds (`12345` → 12.345 s). The roles PDF instead shows `t: 76.6`, which appears to be seconds. No magnitude-based guessing/conversion is performed. Please agree on ms versus seconds, elapsed versus epoch origin, and reboot behavior before using live charts.
2. **Batch timing:** the client provisionally treats `t` as the last/newest sample timestamp in `disp_mm`, ordered oldest to newest, containing only new samples. Confirm this; a rolling window would duplicate data.
3. **Sample rate is not 20 Hz by assumption.** Merged firmware reports ~16.6 D lines/s. Brain can emit a different/resampled cadence. Without a configured sample interval, live chart spacing is estimated as `(current.t - previous.t) / current.disp_mm.length`. The first batch waits for the next timestamp; estimates may be distorted by missing batches or changing batching, and are labeled as estimates.
4. Once the Brain output sample interval is agreed, set the frontend-only `VITE_LIVE_SAMPLE_INTERVAL_MS` to that positive interval and restart Vite (or rebuild). For example, in PowerShell, **only if 60 ms is agreed**:

   ```powershell
   $env:VITE_LIVE_SAMPLE_INTERVAL_MS = '60'
   npm run dev
   ```

   Leave unset to estimate; invalid/nonpositive values also fall back to estimation. This setting never changes the wire protocol. Clear the variable with `Remove-Item Env:VITE_LIVE_SAMPLE_INTERVAL_MS` when finished.
5. Agree on gateway ownership, Brain calling interface, status cadence, alert schema, sensor-loss signaling, and source provenance. The client creates no gateway and opens no serial port. A connected WebSocket alone does not prove a working sensor.

## Verification

`npm test` covers status validation, alert separation, stale traffic, reconnect/backoff, constructor failures, cleanup/late callbacks, waveform timing/reset/history bounds, and all five demo states. `npm run build` checks TypeScript and production bundling.

Manual checks (future gateway required for LIVE data):

1. In DEMO select every state; check waveform, BPM, motion, presence and simulation labels.
2. Switch to LIVE with no server: connecting/disconnected, blank readings, no mock waveform. Switch back: demo works again.
3. With the agreed server, send the example JSON with increasing millisecond `t`; verify received values and waveform. First two batches are required when timing is estimated.
4. Send invalid JSON, invalid status fields and alert events: no crash, no status replacement by events.
5. Pause valid statuses for >3 seconds: stale and blank readings, even if alert/invalid traffic continues. Resume to recover.
6. Stop/restart the server: automatic reconnect, no readings before a valid status arrives. Repeated mode switches should not leave open sockets/timers. Check mobile and keyboard controls.

Actual gateway/sensor integration and browser visual QA are separate from the fake-socket tests.
