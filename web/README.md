# IBUKI Web — DEMO / team-server LIVE

React + TypeScript + Vite + Recharts. Uses the existing `pc/ibuki/server.py` at `/ws` and `/api/health`; no separate Python gateway. This branch may not yet contain `pc/`: the integration tools below run an unchanged, fetched Git snapshot in an owned temporary directory, without merging or modifying `pc/` or `firmware/`.

No medical judgement, infant thresholds, SIDS prevention capability, phone requests, or device commands are implemented in the web client. The team server has notification capabilities; the integration tools explicitly disable them and select the non-alerting placeholder.

## Local development

Node.js 22.12+ recommended (22+ required for the native-WebSocket integration checker).

```sh
cd web
npm install
npm run dev
```

Open Vite's printed URL, normally http://127.0.0.1:5173. Default DEMO runs independently with all five scene controls and simulation labels. Select LIVE to subscribe to the team server. Mode changes close sockets, abort health requests, cancel timers, and clear prior readings/events/history.

Vite proxies `/ws` and `/api` to the laptop's `127.0.0.1:8000`. Production uses the page's host and port: the team server serves `web/dist` after `npm run build`. Phones opening `http://<laptop>:8000/` connect to that laptop, not their own localhost. For Vite on another device, start it with `npm run dev -- --host 0.0.0.0`.

Optional build/dev configuration:

- `VITE_LIVE_WS_URL`: full WebSocket URL or relative `/ws`; default `/ws` on the page origin, using `wss` for HTTPS.
- `VITE_LIVE_HEALTH_URL`: health URL override; default `/api/health` on the WebSocket server's HTTP(S) origin.
- `VITE_LIVE_SAMPLE_INTERVAL_MS`: positive fallback sample spacing only when a status omits `dt_ms`. Otherwise timing is estimated from consecutive batches. No live 20 Hz assumption.

Restart Vite or rebuild after setting variables. Prefer the default same-origin proxy: a cross-origin health URL requires server CORS support, which the current team server does not configure. Health failure is handled as unverified provenance; it does not crash the page or replace readings with mocks.

## Team messages

```json
{"t":123456,"state":"OK","bpm":null,"disp_mm":[0.12,0.15],"dt_ms":60,"motion":12.5,"present":true,"source":"placeholder","shaking":false,"chest_cm":48.75}
```

The six core fields remain `t`, `disp_mm`, `bpm`, `motion`, `present`, `state`; there are no new mandatory keys. Optional `dt_ms`, `source`, `shaking` are read directly from the existing server; other fields are safely ignored. `bpm` may be null. The five states are EMPTY, OK, STILL, CHECK, ALERT. Array length is bounded at 4096; empty arrays are valid. Nonfinite numbers, wrong core types, invalid optional-field types, or invalid states are rejected without crashing.

- **Time:** `t` is milliseconds since the board/replay start, marking the newest sample. Point `i` of `n` is at `t - (n - 1 - i) * dt_ms`. This matches `pc/README.md` and the placeholder implementation. The roles PDF's seconds-like `t:76.6` example is older/ambiguous; a future Brain must preserve ms or agree a change explicitly. No magnitude guessing occurs.
- **Interval priority:** per-message `dt_ms` → positive frontend fallback → estimated `(current.t - previous.t) / n`. Estimation waits for two reliable batches and is visibly labeled. Only new samples are expected, oldest first; rolling windows would duplicate data.
- **Empty/shaking:** `disp_mm: []` clears waveform history. `shaking: true` hides/clears the wave even if samples are present and shows an unreliable/unavailable notice, never a no-breathing judgement. No zero samples are fabricated. BPM is also hidden while the wave is unavailable.
- **Placeholder:** status `source: "placeholder"` or health `brain: "placeholder"` is visibly labeled provisional processing, never a breathing verdict. BPM is always hidden even if a malformed producer supplies a numeric value. Placeholder input can be board or replay; it is not automatically simulated input.

Separate messages:

```json
{"type":"alert","reason":"no_breath","since_s":10}
{"type":"board","connected":false}
```

Alert events are displayed as records, not substituted for status. Board input connection is independent from WebSocket connection. Board false immediately hides readings and clears the waveform generation; reconnect waits for advancing timestamps. Board events do not refresh the status freshness timer. Unsupported events, including `type: "source"`, are ignored safely; no custom provenance envelope is required.

## Freshness, source and cleanup

- WebSocket connecting/connected/disconnected/stale states remain visible. Closed sockets retry with a 1/2/4/8/10-second capped backoff. Only a valid status resets the 3-second stale timer; valid empty-sample statuses still count as activity. The timer is transport freshness, not a medical threshold.
- The server initially sends cached `hub.latest`; LIVE waits for two statuses with progressing `t` before displaying current values. Stale/disconnected, a board disconnect, and a mode change hide values and reset waveform history. A clock restart or gap clears waveform continuity. Notification history is separate and survives reconnection within the LIVE session.
- `/api/health` is polled without overlap every three seconds after completion, with a two-second request timeout and abort on cleanup. `source: BoardLink` is labeled board input (accuracy unverified); `FileReplay` is labeled **recording replay, not live measurement**. Failed/invalid health responses clear provenance to unknown. The WebSocket board event is authoritative for input connection; health board state is not substituted for it.
- Polling means provenance changes may take one polling cycle to appear. The protocol does not attest accuracy or the true origin of replay files. No verified-breathing claim is made from a successful socket/health request.
- DEMO's 16.2 BPM and movement cutoff are illustrative only. LIVE never applies that movement cutoff and only displays the received motion index.

## Notification history and current warning indications

`src/data/notifications.ts` is a pure, gateway-independent reducer. Both alert and info callbacks record original payload fields plus a local receipt time and unique local ID. Repeated reasons are not deduplicated. The newest 20 received events are listed first in `NotificationPanel`; there is no storage, database, notification acknowledgment, or server command. Existing mode behavior is preserved: switching DEMO/LIVE remounts the session and resets its memory, as does a full reload. A WebSocket reconnect does **not** reset it.

Current warnings are separate from history, at most one latest indication per received alert reason. Info events never activate a warning. An old warning can remain indicated even if its receipt has aged out of the 20-item history. This avoids silently removing a current indication because unrelated info arrived.

The inspected Brain/server emits statuses and alert/info events, with no explicit resolution event. A processing cycle can send a status followed immediately by an event. Only subsequent, strictly progressing statuses can change a received warning's indication; rendering the preceding status again cannot dismiss it. The optional event `t`, when present, is also an ordering barrier. The reducer never edits the status, BPM, wave samples or WebSocket stale timer.

- `no_breath`: a received warning stays indicated with present-target CHECK/ALERT states. A later present-target OK/STILL status moves it out of the current area, leaving its history. The label is `現在の解析状態では継続していません`, not confirmed breathing, safety, or medical recovery. EMPTY alone leaves this previous breathing-motion condition unknown.
- `left_bed`: only a received event creates this warning; EMPTY alone creates nothing. It stays indicated with EMPTY/absent statuses and leaves the current area when a later status reports present and non-EMPTY. Inconsistent fields leave it unknown.
- Disconnect, stale status stream, board false, shaking, or provisional placeholder processing leave outstanding indications **unknown**, not resolved. Reconnect requires two progressing statuses before evaluating them. A timestamp reset also requires new progress. Previously non-indicated warnings are not reactivated merely by disconnection.
- The UI displays local receipt time as `受信時刻`. Optional server `t` stays an explicitly labeled source-data time in milliseconds; it is never fabricated or treated as Unix wall time. Positive `since_s` is shown for no_breath/fast_breathing only; instantaneous zero-second durations are omitted from the display while preserved in payload data.

Protocol limits: there are no unique event IDs, replay/backlog, definitive resolution messages, or clock-epoch markers. Repeated deliveries are therefore retained as separate receipts, and missed events while disconnected cannot be reconstructed. Consecutive increasing timestamps are a conservative freshness proxy, not proof of live sensor data or accuracy. Connection/clock-reset boundaries discard old-clock ordering barriers and require new progress before reevaluation. Browser event receipt does not confirm smartphone push delivery.

`tests/notifications.test.mjs` covers the lifecycle through pure functions. The existing source suite also wires the reducer into fake WebSocket callbacks to test reconnect, cache, cleanup and event/wave isolation together.

### Latest-main integration checks (PRs #29, #30 and #31)

Task 2 was restored onto main `2082248776330292c8ca4513ea776adf9717efe0` with all seven original files preserved. PR #29's launcher and server-served build remain compatible. PR #30 sends the CHECK nudge through the server's internal push path only: CHECK remains a status, not a new WebSocket notification reason. The client rejects invented `alert/check` or `info/check` events. PR #31 changes the Brain's departure detection to echo-based presence, without changing the four notification formats. This UI uses received events and presence/status, never movement thresholds, to present departure warnings.

LIVE waveform investigation: the existing port-8000 server reported `/api/health` source `FileReplay`, Brain `Brain`, and ntfy disabled. Its running launcher was `.venv/ibuki-test-data/serve_test.py`, configured with `--replay .venv/ibuki-test-data/fake_board_for_generation.csv`; the waveform therefore came from synthetic recorded test data, not a physical radar. Separately, Playwright screenshots used explicitly mocked `/ws` messages and FileReplay health metadata. LIVE does not subscribe to the frontend demo source and never falls back to it; replay is visibly labeled. No P0 source/connection-state defect was found in this investigation.

Post-integration verification: 33 frontend tests passed; build and `git diff --check` passed. Actual Edge/Playwright scenarios A–E and desktop 1440×900 / mobile 390×844 / 320×700 layout checks passed, with no console/page errors or horizontal overflow. A latest-team-server replay check also passed, using synthetic fixture data, not sensor readings. Existing P2 observations (small muted text, narrow-screen warning position, history scroll affordance) are unchanged and were not fixed in this integration.

## Tests and browser verification without merging pc/

From the repository root, prepare the team's Python dependencies in a virtual environment:

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install fastapi uvicorn numpy requests python-dotenv pyserial websockets
.\.venv\Scripts\python.exe web/scripts/test_pc_integration.py
```

```sh
# macOS
python3 -m venv .venv
./.venv/bin/python -m pip install fastapi uvicorn numpy requests python-dotenv pyserial websockets
./.venv/bin/python web/scripts/test_pc_integration.py
```

The tool reads the already fetched `origin/main` (`--ref` is configurable) with `git show`, copies its actual `pc/` code into a temporary folder, and starts that original server on an ephemeral local port. It prefers a local `recordings/*.csv` or `--replay path.csv`; if none exists, it creates a clearly identified **synthetic replay** using the team's unchanged `pc/tests/synth.py` fixture. It does not create another server implementation. The native TypeScript client receives actual statuses/board events and checks placeholder, replay, timing and waveform; the synthetic test additionally checks shaking and empty samples. All child processes/temporary files are owned by the test and cleaned up on exit. It requires no serial device and sends no client commands. Environment-file loading is disabled and the ntfy topic is explicitly empty.

For browser QA on this branch, build once, then keep that unchanged server snapshot running:

```powershell
cd web
npm run build
cd ..
.\.venv\Scripts\python.exe web/scripts/test_pc_integration.py --serve
```

On macOS replace the interpreter with `./.venv/bin/python`. Open **http://127.0.0.1:8000**, select **LIVE**, and confirm `録画再生`, `暫定処理`, `placeholder`, BPM `—`, waveform after learning, and unavailable-wave notices during shaking. The synthetic fixture loops, so transitions repeat. Stop with Ctrl+C. Alternatively leave the snapshot server running and use `npm run dev` for the Vite proxy. Real sensor integration remains separate from synthetic replay verification.

After `pc/` is available in the working tree through the team's normal review process, run the existing server directly (no test snapshot needed), for example:

```sh
python pc/ibuki/server.py --brain placeholder --replay recordings/your-file.csv --loop
```

Consult `pc/README.md` for real server setup and notification configuration. The web never calls `/api/test-notification` or sends `type: button`.

Frontend checks:

```sh
cd web
npm test
npm run build
```

Unit tests cover core/optional status validation, empty/shaking/disconnected/placeholder behavior, dt_ms timing, fallback estimation, endpoint derivation, health failures/cleanup, ignored events, alert routing, retries, stale data and DEMO states. The integration checker uses the same TypeScript source over a real socket; it is not browser visual QA.
