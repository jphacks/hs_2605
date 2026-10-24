# IBUKI PC side (server, recorder, placeholder Brain)

[English](#english) | [日本語](#日本語)

---

## English

The laptop side of IBUKI. The server reads the radar board over USB, feeds every line to the Brain, and pushes the Brain's results to the web dashboard over WebSocket. It also sends the phone/wearable notification (ntfy) and switches the board's red LED. Everything runs locally. Not a medical device.

| File | What it does |
|---|---|
| [ibuki/server.py](ibuki/server.py) | Server: board → Brain → dashboard (`ws://localhost:8000/ws`), LED, ntfy |
| [ibuki/serial_reader.py](ibuki/serial_reader.py) | Line parser, lost-line counter, board link (reconnects after an unplug), recording replay |
| [ibuki/placeholder_brain.py](ibuki/placeholder_brain.py) | Stand-in Brain until the real one lands: breathing wave, motion, presence. **No bpm, no alerts.** |
| [ibuki/shake.py](ibuki/shake.py) | Detects the board itself moving (all calm echoes jump together) |
| [record.py](record.py) | Saves raw board lines to `recordings/rec_<name>_<time>.csv` |
| [tests/](tests/) | 19 tests, including end-to-end server tests |

### Run (repo root, venv one folder up)

```
../env/bin/python pc/ibuki/server.py                      # live board
../env/bin/python pc/ibuki/server.py --replay recordings/<file>.csv --loop   # no board needed
```

Check it: open http://localhost:8000/api/health. `board_connected` should be `true`.

While the server runs it owns the USB port. Use `--server` for the other tools:

```
../env/bin/python firmware/tools/live_view.py --server
../env/bin/python pc/record.py breathing_50cm --seconds 60 --server
```

### Notifications to the phone and watch (ntfy)

1. On the iPhone, install **ntfy** from the App Store.
2. Tap **+**, enter the topic from the repo's `.env` file (`IBUKI_NTFY_TOPIC=...`), keep the server `ntfy.sh`, and subscribe. Allow notifications.
3. Apple Watch: in the iPhone's Watch app → Notifications, turn on mirroring for ntfy.
4. Test (server running): `curl -X POST http://localhost:8000/api/test-notification`. The phone shows 「テスト通知です（IBUKI）」.

The topic is like a password: anyone who knows it can read and send. `.env` is git-ignored; never commit it. Without internet the push fails, but the LED and the dashboard alert keep working.

### For the Brain owner: the interface

Put a class `Brain` in `pc/ibuki/brain.py` (the server finds it automatically), or start the server with `--brain module:Class`.

```python
class Brain:
    def feed(self, line: str) -> list[dict]:
        ...  # called for every board line (C, D, S, B and junk). Return [] most of the time.
```

- **Status**, 5–10 times per second:
  `{"t": board_ms, "state": "EMPTY|OK|STILL|CHECK|ALERT", "bpm": number or None, "disp_mm": [new samples], "dt_ms": 60, "motion": number, "present": bool, "chest_cm": number}`
- **Event**: `{"type": "alert", "reason": "no_breath", "since_s": 10}` → the server turns the LED red and sends the push. Leaving `ALERT` turns the LED off.
- `B,1` (board button, or the dashboard's cancel) arrives as a normal line.
- A crash inside `feed()` is logged and skipped; the server keeps running.

Rules from the board data: D lines come every ~60 ms (16.6 Hz, not 20 Hz). After `S,...,radar restart: ...` or a `t_ms` jump over 0.5 s, restart phase tracking. Treat a board shake (see `ibuki/shake.py`) as breathing **unknown**, never as "no breathing". The data spec is in [firmware/README.md](../firmware/README.md).

### For the dashboard: messages on `ws://<laptop>:8000/ws`

| Message | Meaning |
|---|---|
| Status (fields above) | Placeholder statuses also carry `"source": "placeholder"` and `"shaking": true/false`. While shaking, `disp_mm` is empty. |
| `{"type": "alert", ...}` | Alert event from the Brain |
| `{"type": "board", "connected": true/false}` | Sent on connect and when the USB board drops or comes back |

Place the samples with `dt_ms`: sample `i` of `n` is at `t - (n - 1 - i) * dt_ms`. The dashboard may send `{"type": "button"}` to cancel, like the board button. If `web/dist` exists (`npm run build`), the server also serves the dashboard at http://<laptop-ip>:8000 for phones on the same Wi-Fi.

### Recording plan (60 s each, ~230 KB each)

```
../env/bin/python pc/record.py <name> --seconds 60       # add --server if the server is running
```

| Name | What to do |
|---|---|
| `empty` | Nobody within 1.2 m of the board |
| `breathing_20cm` | Chest 20 cm from the board, still, normal breathing (near test) |
| `breathing_30cm`, `breathing_50cm` | Same at 30 cm and 50 cm |
| `hold` | 20 s breathing, 20 s holding breath, 20 s breathing |
| `moving` | Shift around, move arms |
| `shake` | Breathing still, then tap or bump the table/board a few times |
| `leave` | 20 s breathing, then walk away |

Files go to `recordings/` and are git-ignored. To share one, copy it to `recordings/<name>.sample.csv`.

### Tests

```
../env/bin/python -m unittest discover -s pc/tests -t pc
```

### Troubleshooting

- **`board_connected: false`**: the cable, or another program holds the port (close the viewer, or use `--server`).
- **"team Brain not found, using the PLACEHOLDER"**: expected until `pc/ibuki/brain.py` exists.
- **No push**: check `"ntfy": true` in `/api/health`, the topic in the app, and the internet.

---

## 日本語

IBUKI の PC 側です。サーバーが USB でレーダーボードを読み、すべての行を Brain に渡し、結果を WebSocket で Web ダッシュボードに送ります。スマートフォン・ウェアラブルへの通知（ntfy）とボードの赤 LED も担当します。すべてローカルで動作します。医療機器ではありません。

| ファイル | 内容 |
|---|---|
| [ibuki/server.py](ibuki/server.py) | サーバー：ボード → Brain → ダッシュボード（`ws://localhost:8000/ws`）、LED、ntfy |
| [ibuki/serial_reader.py](ibuki/serial_reader.py) | 行の解析、欠落行のカウント、ボード接続（抜き差し後に自動再接続）、録画の再生 |
| [ibuki/placeholder_brain.py](ibuki/placeholder_brain.py) | 本物の Brain ができるまでの仮の Brain：呼吸の波形・動き・在室。**呼吸数なし、アラートなし。** |
| [ibuki/shake.py](ibuki/shake.py) | ボード自体の揺れを検出（静止した反射がすべて同時に動く） |
| [record.py](record.py) | ボードの生データを `recordings/rec_<名前>_<時刻>.csv` に保存 |
| [tests/](tests/) | テスト 19 件（サーバーのエンドツーエンドテストを含む） |

### 実行（リポジトリのルート、venv は 1 つ上のフォルダ）

```
../env/bin/python pc/ibuki/server.py                      # 実機のボード
../env/bin/python pc/ibuki/server.py --replay recordings/<ファイル>.csv --loop   # ボードなし
```

確認：http://localhost:8000/api/health を開き、`board_connected` が `true` になっていること。

サーバーの実行中は USB ポートをサーバーが使います。他のツールには `--server` を付けてください：

```
../env/bin/python firmware/tools/live_view.py --server
../env/bin/python pc/record.py breathing_50cm --seconds 60 --server
```

### スマートフォン・Apple Watch への通知（ntfy）

1. iPhone に App Store から **ntfy** をインストール。
2. **+** を押し、リポジトリの `.env` にあるトピック（`IBUKI_NTFY_TOPIC=...`）を入力し、サーバーは `ntfy.sh` のまま購読。通知を許可。
3. Apple Watch：iPhone の Watch アプリ → 通知 で ntfy のミラーリングをオン。
4. テスト（サーバー実行中）：`curl -X POST http://localhost:8000/api/test-notification`。スマートフォンに「テスト通知です（IBUKI）」が届きます。

トピックはパスワードと同じで、知っている人は誰でも読み書きできます。`.env` は git の対象外です。絶対にコミットしないでください。インターネットがないと通知は失敗しますが、LED とダッシュボードの警告は動き続けます。

### Brain 担当の方へ：インターフェース

`pc/ibuki/brain.py` にクラス `Brain` を置くと、サーバーが自動で使います（または `--brain モジュール:クラス` で指定）。

```python
class Brain:
    def feed(self, line: str) -> list[dict]:
        ...  # ボードの各行（C・D・S・B・ゴミ）ごとに呼ばれる。ほとんどの場合 [] を返す。
```

- **状態**（1 秒に 5〜10 回）：
  `{"t": ボードのms, "state": "EMPTY|OK|STILL|CHECK|ALERT", "bpm": 数値 または None, "disp_mm": [新しいサンプル], "dt_ms": 60, "motion": 数値, "present": bool, "chest_cm": 数値}`
- **イベント**：`{"type": "alert", "reason": "no_breath", "since_s": 10}` → サーバーが LED を赤にして通知を送ります。`ALERT` から抜けると LED は消えます。
- `B,1`（ボードのボタン、またはダッシュボードの取り消し）は普通の行として届きます。
- `feed()` 内で例外が起きてもログに残してスキップし、サーバーは止まりません。

ボードのデータについて：D 行は約 60 ms ごと（16.6 Hz、20 Hz ではありません）。`S,...,radar restart: ...` の行、または 0.5 秒を超える `t_ms` の飛びのあとは位相の追跡をやり直してください。ボードの揺れ（`ibuki/shake.py`）は呼吸「不明」として扱い、「呼吸なし」とは判定しないでください。データの仕様は [firmware/README.md](../firmware/README.md) にあります。

### ダッシュボード向け：`ws://<PC>:8000/ws` のメッセージ

| メッセージ | 意味 |
|---|---|
| 状態（上記のフィールド） | 仮の Brain の状態には `"source": "placeholder"` と `"shaking": true/false` も付きます。揺れている間 `disp_mm` は空です。 |
| `{"type": "alert", ...}` | Brain からのアラート |
| `{"type": "board", "connected": true/false}` | 接続時と、USB のボードが外れた・戻ったとき |

サンプルの時刻は `dt_ms` で決めてください：`n` 個中 `i` 番目は `t - (n - 1 - i) * dt_ms`。ダッシュボードから `{"type": "button"}` を送ると、ボードのボタンと同じく取り消しになります。`web/dist` があれば（`npm run build`）、同じ Wi-Fi のスマートフォンから http://<PCのIP>:8000 でダッシュボードを開けます。

### 録画の予定（各 60 秒、各約 230 KB）

```
../env/bin/python pc/record.py <名前> --seconds 60       # サーバー実行中は --server を付ける
```

| 名前 | 内容 |
|---|---|
| `empty` | ボードから 1.2 m 以内に誰もいない |
| `breathing_20cm` | 胸をボードから 20 cm、静止して普通に呼吸（近距離のテスト） |
| `breathing_30cm`、`breathing_50cm` | 30 cm、50 cm で同様に |
| `hold` | 呼吸 20 秒、息止め 20 秒、呼吸 20 秒 |
| `moving` | 体を動かす、腕を動かす |
| `shake` | 静かに呼吸し、途中で机やボードを数回たたく・揺らす |
| `leave` | 20 秒呼吸してから立ち去る |

ファイルは `recordings/` に保存され、git の対象外です。共有するときは `recordings/<名前>.sample.csv` にコピーしてください。

### テスト

```
../env/bin/python -m unittest discover -s pc/tests -t pc
```

### トラブルシューティング

- **`board_connected: false`**：ケーブルを確認。または他のプログラムがポートを使っています（ビューアを閉じるか `--server` を使う）。
- **「team Brain not found, using the PLACEHOLDER」**：`pc/ibuki/brain.py` ができるまでは正常です。
- **通知が来ない**：`/api/health` の `"ntfy": true`、アプリのトピック、インターネット接続を確認。
