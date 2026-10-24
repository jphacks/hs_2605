# IBUKI Web / モックダッシュボード

React + TypeScript + Vite + Recharts. All readings are simulated. No sensor connection, phone notifications, diagnosis, or SIDS prevention capability is implemented.

## Start locally

Requires Node.js 20.19+ or 22.12+.

```sh
cd web
npm install
npm run dev
```

Open the URL printed by Vite (normally http://127.0.0.1:5173).

```sh
npm run build
npm run preview
```

## Data boundary

- `src/data/status.ts`: dashboard-local TypeScript mirror of the existing status structure; does not modify the shared protocol.
- `src/data/mockSource.ts`: simulated source, delivering four 20 Hz displacement samples every 200 ms. `t` is elapsed milliseconds in this mock, following the data-spec example. Confirm timestamp units with the Brain owner before integration.
- `src/useDashboard.ts`: subscribes, cleans up, and retains at most 30 seconds / 600 points.
- `src/App.tsx`: Japanese UI and manual scenario selection.

Status fields remain exactly `t`, `disp_mm`, `bpm`, `motion`, `present`, `state`. Missing BPM is `null`; it is never displayed as zero. Demo BPM 16.2 is illustrative, not an infant reference range. Movement labels use an illustrative UI cutoff, not a clinical decision rule. CHECK/ALERT are manually selected scenarios; no detection algorithm is implemented.

For integration, implement `StatusSource.subscribe(listener)` using `ws://localhost:8000/ws`, unsubscribe by closing the socket, and replace `createMockSource` in App. Validate incoming statuses and handle disconnects and non-status alert events separately. Remove mock controls and replace simulation labels only once real data is connected. Keep source/provenance explicit. Wave sample spacing currently assumes the specified 20 Hz source; confirm that with the Brain owner.

## Manual demo check

Select OK: moving waveform, 16.2 BPM, movement and presence shown. STILL: waveform continues with low movement. CHECK and ALERT: flat-ish illustrative wave, unknown BPM, distinct notice. EMPTY: no presence, unknown BPM. Check keyboard selection and narrow/mobile layout. The simulation continues until the page is closed; old waveform points roll off after 30 seconds. The sample deliberately keeps real past points when switching scenes.
