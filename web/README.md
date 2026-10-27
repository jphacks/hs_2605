# IBUKI web dashboard

Issue #70 のテーマ・昼夜の比較画像・ブラウザー検証 / Theme, before/after screenshots and browser verification: [Design notes](docs/design/issue-70/README.md).

[日本語](#日本語) | [English](#english)

## 日本語

IBUKI のブラウザ用ダッシュボードです。**React、TypeScript、Vite、Recharts** を使っています。PC の Brain が解析した結果を WebSocket で受信し、呼吸に関連する動き、推定呼吸数、在床状態、通知を表示します。レーダーの読み取りや呼吸の判定は Web 側では行いません。サーバーと通知は [pc/README.md](../pc/README.md)、レーダーのファームウェアは [firmware/README.md](../firmware/README.md) を参照してください。

### 動かし方

Windows 10/11 はリポジトリのルートで `.\ibuki.cmd` を実行します（PowerShell / CMD 共通）。macOS の `./ibuki.command` と同じく、Python 環境・Web ビルドを準備し、health 応答後に画面を開きます。`.\ibuki.cmd --replay recordings/samples/hold.sample.csv` のように、3 種類の [サンプル録画](../README.md#動かし方) をボードなしで再生できます。Vite の開発手順と LIVE/DEMO の動作は変わりません。

macOS では、リポジトリのルートから次を実行するのが簡単です：

```sh
./ibuki.command
```

必要な準備とダッシュボードのビルドを行い、PC サーバーを起動して **http://localhost:8000** を開きます。画面は最初から **LIVE** です。録画データを再生したい場合：

```sh
./ibuki.command --replay recordings/<file>.csv
```

Web 側を開発するときは、Node.js **22.12 以上**を用意して次を実行します：

```sh
cd web
npm install
npm run dev
```

**http://localhost:5173** を開きます。通常は LIVE なので、実際のデータを見るには PC サーバーも別に起動してください。Vite は `/ws` と `/api` を `127.0.0.1:8000` に転送します。`npm run build` の出力は `web/dist` に作られ、PC サーバーから配信されます。

ボードやサーバーなしで **5 つの模擬状態**を試すには、開発サーバーの **http://localhost:5173/?demo=1** を開いてください。DEMO は模擬データだけを使い、通知や機器への命令は送りません。本番環境では `?demo=1` を付けても LIVE のままです。LIVE で接続に失敗しても、模擬データへ自動的に切り替わることはありません。

### 画面に表示するもの

- **接続状況：** 画面上部にサーバー接続、センサー入力、データ更新の状態を簡潔に表示します。**接続の詳細**を開くと、WebSocket URL、入力元、Brain の処理モードなどを確認できます。WebSocket がつながっただけでは、実機センサーの接続が確認できたことにはなりません。
- **現在の状態：** Brain から受信した解析状態を、メインの README やスマートフォンの通知と同じ言葉で表示します（下の表）。ボードが揺れているときは「センサーが揺れています」と表示します。Web 側で呼吸の安全性を判定しているわけではありません。
- **波形と呼吸数：** 胸の変位データ（直近 30 秒）をグラフで表示し、Brain の呼吸数を示します。利用できない値は `—` と表示し、存在しない波形を作りません。波形のない更新（揺れ・学習中・不在）では、描いた波形を消さずに残し、「測定なし」を灰色の点線で示します。再起動したように見えないためです。グラフを消すのは、ボードの時刻が最初に戻ったとき（再起動）だけです。
- **ベッドと体の動き：** 「います／いません」と「動いています／じっとしています」を、Brain から届いた情報で表示します。
- **通知：** **直近 20 件**の通知を短い一覧で表示します。タイトルと緊急度（【緊急】【注意】【お知らせ】）はスマートフォンと同じです。続いている通知は一覧の上に表示します。

Brain が送る状態は 5 種類です：

| 状態 | 表示 | 意味 |
|---|---|---|
| `EMPTY` | ベッドにいません | ベッド上の対象を検知していません。これだけで離床の警告は作りません。 |
| `OK` | 起きています | 体動を検知しています。動いている間は呼吸に関連する細かな動きを正確に確認できません。 |
| `STILL` | 眠っています | 対象の動きが少なく、Brain が呼吸に関連する動きを検出しています。 |
| `CHECK` | 呼吸を確認中 | 呼吸に関連する動きが現在見えておらず、確認を続けています。 |
| `ALERT` | 呼吸が確認できません | 呼吸に関連する動きが見えない状態が、Brain の警告条件に達しています。 |

いずれも**試作品の解析状態であり、医療的な診断ではありません**。特に `OK` は「呼吸が正常だと確認できた」という意味ではありません。

### LIVE データの流れ

既存の PC サーバーから、**`/ws`** で状態・ボード接続イベント・通知を受信し、**`/api/health`** でデータの入力元や Brain の情報を確認します。Web 用の別の Python サーバーはありません。

受信する状態の例（数値は説明用です）：

```json
{"t":123456,"state":"STILL","bpm":18.2,"disp_mm":[0.12,0.15],"dt_ms":60,"motion":0.8,"present":true}
```

`t` は送信側での経過時間を示す**ミリ秒**で、実際の時刻ではありません。`disp_mm` は新しく取得した変位サンプルで、`bpm` は `null` のこともあります。`source` や `shaking` などは省略可能です。Web 側では受信データの形式を検証します。

- WebSocket が切れると自動で再接続します。正常な状態メッセージが **3 秒間**届かなければ更新停止とみなし、現在値を隠します。
- 接続直後は、サーバーに残っていた前回の状態だけでは表示せず、送信側の時刻が進んだことを確認します。
- ボードと WebSocket の接続は別に扱います。ボードの切断や揺れがあるときは、不確かな数値や架空の波形を表示しません。
- **`BoardLink`** はボードからの入力を示します（測定精度の保証ではありません）。**`FileReplay`** は **録画再生**と表示し、実測ライブと区別します。**`placeholder`** は暫定処理で、確かな呼吸数は示しません。

### 警告と通知

スマートフォン（iPhone）へのプッシュ通知とボードの LED は **PC サーバー側**が担当します。Web は受信したイベントを表示するだけで、通知を送信したりボードへ命令したりしません。

| Web が受信するイベント | 表示（スマートフォンと同じ） |
|---|---|
| `alert/no_breath` | 【緊急】呼吸が確認できません |
| `alert/left_bed` | 【注意】ベッドにいません |
| `info/fast_breathing` | 【注意】呼吸が速い状態が続いています |
| `info/woke_up` | 【お知らせ】起きました |

`CHECK` は **Brain の状態**であり、上記の 4 種類の通知イベントではありません。現在のサーバーは **CHECK だけではスマートフォンへ通知しません**。

現在の警告は、実際に警告イベントを受信したときに表示します。その後の新しい解析状態によって表示が変わることはありますが、**医学的に回復した証明ではありません**。履歴は新しい順に最大 20 件です。WebSocket の再接続では残りますが、ページの再読み込みで消えます。保存用のデータベースはありません。また、Web にイベントが届いても、スマートフォンへの配信成功を保証するものではありません。

### ファイルとテスト

| ファイル | 内容 |
|---|---|
| [src/App.tsx](src/App.tsx) | 画面のレイアウト、LIVE/DEMO |
| [src/WaveChart.tsx](src/WaveChart.tsx) | 呼吸の波形グラフ（直近 30 秒。時間軸は更新の間もなめらかに流れます） |
| [src/ConnectionStatus.tsx](src/ConnectionStatus.tsx) | コンパクトな接続状況と折りたたみ式の詳細 |
| [src/NotificationPanel.tsx](src/NotificationPanel.tsx) | 現在の警告と通知履歴 |
| [src/data/](src/data/) | WebSocket、ヘルス情報、入力元、状態の表示文、波形、通知の処理 |
| [src/useDashboard.ts](src/useDashboard.ts) | データ受信と画面更新 |
| [tests/](tests/) | Web 側の自動テスト |
| [scripts/test_pc_integration.py](scripts/test_pc_integration.py) | チームの PC サーバーを一時コピーして実行する連携テスト |

リポジトリのルートから Web 側のテストとビルドを実行します：

```sh
cd web
npm test
npm run build
```

PC サーバーとの連携テストを行う場合は、ルートの [requirements.txt](../requirements.txt) にある Python パッケージをインストールし、Node.js 22 以上と Web の依存パッケージを用意して、ルートから実行します：

```sh
python web/scripts/test_pc_integration.py
```

このテストは録画データ、または合成したテストデータを使用します。**実際の赤ちゃんでの測定精度は検証しません**。スマートフォン通知は無効にしており、レーダーボードも不要です。

### うまく動かないとき

- **接続待ち・切断：** PC サーバーを起動し、**http://localhost:8000/api/health** を確認してください。開発用の 5173 番ポートだけではレーダーのデータは取得できません。
- **波形や BPM が出ない：** **接続の詳細**を確認してください。ボードの切断、データ更新停止、センサーの揺れ、`placeholder` 処理などが原因として考えられます。
- **実測ではなく録画が表示される：** **録画再生**と表示されている場合、サーバーが `FileReplay` を使用しています。実機の入力を使うにはボードを接続し、`--replay` なしでサーバーを起動してください。
- **スマートフォンに通知が届かない：** [pc/README.md](../pc/README.md) の ntfy 設定を確認してください。ブラウザの通知履歴だけでは、プッシュ通知の到達は確認できません。

**注意：** IBUKI はハッカソンで開発した**試作品であり、医療機器ではありません**。乳児での検証はまだ行っておらず、病気の診断、安全の保証、SIDS（乳幼児突然死症候群）の予防はできません。

## English

This is the browser dashboard for IBUKI, built with **React, TypeScript, Vite and Recharts**. It receives the PC Brain's results through WebSocket and shows breathing-related movement, estimated breathing rate, presence and notifications. The web app does not read the radar directly or make medical decisions; the PC server and Brain do that. See [pc/README.md](../pc/README.md) for the server and alerts, and [firmware/README.md](../firmware/README.md) for the radar board.

### Running it

On macOS, the easiest way is to run this from the repository root:

```sh
./ibuki.command
```

It prepares the dependencies, builds the dashboard when necessary, starts the PC server and opens **http://localhost:8000**. The page starts in **LIVE** automatically. To replay a recording rather than use a connected board:

```sh
./ibuki.command --replay recordings/<file>.csv
```

For frontend development, use Node.js **22.12+** and run:

```sh
cd web
npm install
npm run dev
```

Open **http://localhost:5173**. This also starts in LIVE, so run the PC server separately to receive data. Vite forwards `/ws` and `/api` to `127.0.0.1:8000`. The server serves the production build from `web/dist` after `npm run build`.

To try the five simulated states **without a board or PC server**, open **http://localhost:5173/?demo=1** in the Vite development server. The DEMO uses simulated data and sends no notifications or device commands. It is unavailable in production: even `?demo=1` starts LIVE there. LIVE never falls back to fake data when a connection fails.

### What's on the screen

- **Connection status:** a small Japanese status strip shows whether the server, board input or incoming data needs attention. Open **接続の詳細** for the WebSocket URL, input source, Brain mode and other diagnostics. An open WebSocket alone does not prove that a physical radar is connected.
- **Current state:** the state received from the Brain, in the same plain words as the main README and the phone (table below), plus センサーが揺れています while the board shakes. The dashboard does not independently decide whether breathing is safe.
- **Waveform and breathing rate:** a graph of received chest-displacement samples (last 30 s) and the Brain's breaths per minute. Unavailable values appear as `—`; the frontend does not invent samples. When an update has no wave (shaking, learning, nobody there), the graph keeps what it drew and a grey dashed line marks 測定なし, so it never looks like a restart. It clears only when the board's clock starts again (a reboot).
- **In bed and movement:** います / いません and 動いています / じっとしています, from the Brain's messages.
- **Notifications:** the **20 most recent** events as a short list, with the phone's titles and urgency (【緊急】【注意】【お知らせ】). An event that is still going on is shown above the list.

The Brain sends five states:

| State | Shown as | Meaning |
|---|---|---|
| `EMPTY` | ベッドにいません | The target is not detected in bed. This state alone does not create a `left_bed` alert. |
| `OK` | 起きています | Body movement detected; breathing-related small movements cannot be reliably assessed while moving. |
| `STILL` | 眠っています | The Brain detected breathing-related movement while the target was relatively still. |
| `CHECK` | 呼吸を確認中 | Breathing-related movement is not currently detected; the Brain is still checking. |
| `ALERT` | 呼吸が確認できません | The no-breathing-motion condition has persisted long enough for the Brain's warning threshold. |

These are **prototype analysis states, not diagnoses**. In particular, `OK` does not mean breathing has been medically confirmed as normal.

### How LIVE data arrives

The browser uses the existing PC endpoints: **`/ws`** for statuses, board connection events and notifications, and **`/api/health`** for input source and processing information. There is no separate web-side Python gateway.

A status looks like this (example values only):

```json
{"t":123456,"state":"STILL","bpm":18.2,"disp_mm":[0.12,0.15],"dt_ms":60,"motion":0.8,"present":true}
```

`t` is the sending device's elapsed time in **milliseconds**, not a wall-clock timestamp. `disp_mm` contains new displacement samples, and `bpm` may be `null`. Optional fields include `source` and `shaking`. The web client validates incoming messages before using them.

- The socket reconnects automatically after a connection failure. If valid status messages stop for **3 seconds**, the data is marked stale and the current measurements are hidden.
- After connecting, the dashboard waits for timestamps to advance instead of displaying only the server's cached last message.
- Board connection is tracked separately from WebSocket connection. A disconnected board or shaking sensor makes measurements unavailable rather than producing a made-up waveform.
- **`BoardLink`** means board input (not verified measurement accuracy). **`FileReplay`** is explicitly labeled **録画再生**: recorded data, **not a live physical reading**. **`placeholder`** means provisional processing; it does not produce a validated breathing rate.

### Notifications

The PC server handles phone (iPhone) push notifications and the board's warning LED. The browser **displays received events**; it does not send pushes or issue commands to the board.

| Event received by the web app | Displayed as (the same as the phone) |
|---|---|
| `alert/no_breath` | 【緊急】呼吸が確認できません |
| `alert/left_bed` | 【注意】ベッドにいません |
| `info/fast_breathing` | 【注意】呼吸が速い状態が続いています |
| `info/woke_up` | 【お知らせ】起きました |

`CHECK` is a **Brain status**, not one of these four WebSocket events. The current server does **not** send a phone push for `CHECK` alone.

A current warning is shown only after an alert event was received. Subsequent, newer analysis statuses may change its indication, but this is **not proof of medical recovery**. The recent-event list keeps up to 20 arrivals, newest first. It survives a WebSocket reconnection while the page stays open, but a reload clears it; there is no persistent notification database. A browser receipt does not prove that a phone received the push.

### Files and tests

| File | Purpose |
|---|---|
| [src/App.tsx](src/App.tsx) | Dashboard layout and LIVE/DEMO presentation |
| [src/WaveChart.tsx](src/WaveChart.tsx) | The breathing graph: last 30 s, time axis glides smoothly between updates |
| [src/ConnectionStatus.tsx](src/ConnectionStatus.tsx) | Compact connection strip and expandable diagnostics |
| [src/NotificationPanel.tsx](src/NotificationPanel.tsx) | Current warnings and event history |
| [src/data/](src/data/) | WebSocket, health, source selection, status words, waveform and notification logic |
| [src/useDashboard.ts](src/useDashboard.ts) | Dashboard data subscription |
| [tests/](tests/) | Frontend automated tests |
| [scripts/test_pc_integration.py](scripts/test_pc_integration.py) | Integration check against a temporary copy of the team's actual PC server |

Run the frontend checks from the repository root:

```sh
cd web
npm test
npm run build
```

For the optional PC integration check, first install the Python dependencies from the root [requirements.txt](../requirements.txt), make sure Node.js 22+ and the web dependencies are available, and run from the repository root:

```sh
python web/scripts/test_pc_integration.py
```

This test uses a recording or an explicitly labeled synthetic fixture; it does **not** validate real infant measurements. It disables phone notifications and does not need a connected radar board.

### If something doesn't work

- **Waiting/disconnected:** start the PC server and check **http://localhost:8000/api/health**. The development dashboard at port 5173 is not itself a radar server.
- **No waveform or BPM:** check **接続の詳細**. The board may be disconnected, the data may be stale, the sensor may be shaking, or the Brain may be running in `placeholder` mode.
- **Recorded waveform instead of live radar:** if the source says **録画再生**, the server is using `FileReplay`; connect the board and start the server without `--replay` for physical input.
- **Phone alerts missing:** check the ntfy configuration in [pc/README.md](../pc/README.md). Seeing an event in the browser does not verify push delivery.

**Limit:** IBUKI is a hackathon prototype, **not a medical device**. It has not been validated on infants and cannot diagnose breathing problems, guarantee safety or prevent SIDS.
