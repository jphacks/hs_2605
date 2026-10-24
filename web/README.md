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
- The server initially sends cached `hub.latest`; LIVE waits for two statuses with progressing `t` before displaying current values. Stale/disconnected, a board disconnect, and a mode change hide values and reset history. A clock restart or gap clears waveform continuity.
- `/api/health` is polled without overlap every three seconds after completion, with a two-second request timeout and abort on cleanup. `source: BoardLink` is labeled board input (accuracy unverified); `FileReplay` is labeled **recording replay, not live measurement**. Failed/invalid health responses clear provenance to unknown. The WebSocket board event is authoritative for input connection; health board state is not substituted for it.
- Polling means provenance changes may take one polling cycle to appear. The protocol does not attest accuracy or the true origin of replay files. No verified-breathing claim is made from a successful socket/health request.
- DEMO's 16.2 BPM and movement cutoff are illustrative only. LIVE never applies that movement cutoff and only displays the received motion index.

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
