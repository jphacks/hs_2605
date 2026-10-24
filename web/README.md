# IBUKI Web — DEMO / LIVE

React + TypeScript + Vite + Recharts. Default mode is DEMO. LIVE receives statuses from `ws://localhost:8000/ws`. The existing server is `pc/ibuki/server.py` on `origin/main`; its code and `pc/README.md` were inspected read-only after fetching. They have not been merged into this checkout. There is no separate `gateway/`. The dashboard sends no phone notifications and implements no diagnostic rules, infant alert thresholds, or SIDS prevention capability.

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

DEMO retains five manually selectable states. LIVE has no scenario controls and never falls back to mock data. Switching modes resets readings/history/events and closes the prior subscription. LIVE denotes server receipt, **not verified sensor provenance**. The pending web changes support an optional `type: source` envelope, but the existing PC server does not emit that envelope. With that server, this pending UI currently shows source as unverified. BrainStatus fields are unchanged.

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

1. **Timestamp unit:** the inspected PC placeholder emits board `t_ms` as `t`, matching this client's milliseconds (`12345` → 12.345 s). The roles PDF instead shows `t: 76.6`, which appears to be seconds. No magnitude-based guessing/conversion is performed. Confirm the eventual Brain retains millisecond units and document its clock origin/reboot behavior.
2. **Batch timing:** the client provisionally treats `t` as the last/newest sample timestamp in `disp_mm`, ordered oldest to newest, containing only new samples. Confirm this; a rolling window would duplicate data.
3. **Sample rate is not 20 Hz by assumption.** Merged firmware reports ~16.6 D lines/s. Brain can emit a different/resampled cadence. Without a configured sample interval, live chart spacing is estimated as `(current.t - previous.t) / current.disp_mm.length`. The first batch waits for the next timestamp; estimates may be distorted by missing batches or changing batching, and are labeled as estimates.
4. Once the Brain output sample interval is agreed, set the frontend-only `VITE_LIVE_SAMPLE_INTERVAL_MS` to that positive interval and restart Vite (or rebuild). For example, in PowerShell, **only if 60 ms is agreed**:

   ```powershell
   $env:VITE_LIVE_SAMPLE_INTERVAL_MS = '60'
   npm run dev
   ```

   Leave unset to estimate; invalid/nonpositive values also fall back to estimation. This setting never changes the wire protocol. Clear the variable with `Remove-Item Env:VITE_LIVE_SAMPLE_INTERVAL_MS` when finished.
5. Reuse `pc/ibuki/server.py`; it already owns the board connection, calls `Brain.feed(line)`, and forwards statuses/events. The server also exposes `/api/health`, `/api/status`, and `/ws/raw`. Do not implement a parallel server. A connected WebSocket alone does not prove a working sensor.

## Existing PC server: pending web integration adjustments

The uncommitted web edits remain available for review. This cleanup does **not** claim that the following adaptations are implemented:

- Read optional `dt_ms` directly from each status and use it for sample placement (`t - (n - 1 - i) * dt_ms`). The current interval handling uses a frontend override, optional source envelope, or timestamp estimation; it ignores the PC server's `dt_ms`.
- Accept `disp_mm: []` as a valid status with unavailable waveform. The placeholder uses this while learning or when `shaking: true`. The current committed validator rejects empty arrays, so it can incorrectly turn these updates into stale data. Do not substitute a flat zero line.
- Surface status `source: "placeholder"` and `shaking` explicitly. Placeholder means provisional processing of board/replay input, not automatically simulated input or a real breathing verdict. It always returns `bpm: null` and never emits alerts. The pending `type: source` support is an optional extension, not a requirement of the existing PC server.
- Handle `{ "type": "board", "connected": false }` separately from WebSocket connection state. The current client ignores it. Clear/hide stale sensor values promptly on board disconnection instead of relying only on the three-second transport timer.
- Determine board versus replay input through `/api/health` (`source` is `BoardLink` or `FileReplay`; `brain` identifies the processor). Replay must be visibly labeled. The initial WebSocket status may be cached; do not present it as a newly measured sensor sample without freshness/source checks.
- For access from another phone/computer, derive the WebSocket host from the page or provide an explicit URL setting. The current hardcoded `localhost` points to that other device, not the laptop serving the page. Keep `/ws/raw` out of the status parser.
- The PC server can send ntfy notifications when configured. The web currently invokes neither notification endpoints nor button commands. Do not add automatic calls to `/api/test-notification` or `{ "type": "button" }` during review/demo. Those have side effects.

These differences were established by reading `origin/main:pc/ibuki/server.py`, `placeholder_brain.py`, and `pc/README.md`, without checking them out or modifying `pc/` or `firmware/`.

## Verification

`npm test` covers status validation, alert separation, stale traffic, reconnect/backoff, constructor failures, cleanup/late callbacks, waveform timing/reset/history bounds, and all five demo states. `npm run build` checks TypeScript and production bundling.

Manual checks (use the existing PC server after its changes are reviewed and merged separately):

1. In DEMO select every state; check waveform, BPM, motion, presence and simulation labels.
2. Switch to LIVE with no server: connecting/disconnected, blank readings, no mock waveform. Switch back: demo works again.
3. With the agreed server, send the example JSON with increasing millisecond `t`; verify received values and waveform. First two batches are required when timing is estimated.
4. Send invalid JSON, invalid status fields and alert events: no crash, no status replacement by events.
5. Pause valid statuses for >3 seconds: stale and blank readings, even if alert/invalid traffic continues. Resume to recover.
6. Stop/restart the server: automatic reconnect, no readings before a valid status arrives. Repeated mode switches should not leave open sockets/timers. Check mobile and keyboard controls.

The retained optional source-envelope test uses a fake socket and has no Python dependency. Changing optional metadata does not reconnect the socket. The duplicate Python server and its exclusive smoke script have been removed. Actual PC-server integration and browser visual QA remain separate from the passing frontend unit tests/build.
