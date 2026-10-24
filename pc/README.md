# IBUKI on the PC

[English](#english) | [日本語](#日本語)

## English

This is the laptop side of IBUKI. [ibuki/server.py](ibuki/server.py) reads the radar board over USB, hands every line to the Brain, and sends the Brain's results to the dashboard over a WebSocket. It also turns the board's red LED on during an alert and sends the notification to the phone.

### Running it

From the repo folder:

```
../env/bin/python pc/ibuki/server.py
```

Then open http://localhost:8000/api/health and check that `board_connected` is `true`. If you don't have the board with you, replay a recording instead:

```
../env/bin/python pc/ibuki/server.py --replay recordings/<file>.csv --loop
```

Only one program can have the serial port open. While the server runs, start the live viewer and the recorder with `--server` so they read through it:

```
../env/bin/python firmware/tools/live_view.py --server
../env/bin/python pc/record.py breathing_50cm --seconds 60 --server
```

### What's in here

[ibuki/serial_reader.py](ibuki/serial_reader.py) parses the board's lines, counts lost lines and reconnects on its own when the USB cable gets pulled.

[ibuki/placeholder_brain.py](ibuki/placeholder_brain.py) stands in until the real Brain is ready. It shows the breathing wave, movement and whether someone is there, but it never gives a breathing rate and never raises an alert.

[ibuki/shake.py](ibuki/shake.py) notices when the board itself is moving. Normally the walls and furniture don't move, so if all of their echoes jump at once, it's the board.

[record.py](record.py) saves the raw board lines to `recordings/rec_<name>_<time>.csv`.

[tests/](tests/) has 19 tests, including ones that start the real server. Run them with:

```
../env/bin/python -m unittest discover -s pc/tests -t pc
```

### Notifications

We use ntfy. Install the ntfy app on the iPhone, tap +, and subscribe to the topic in the repo's `.env` file (the line `IBUKI_NTFY_TOPIC=...`). To get the notifications on the Apple Watch too, turn on mirroring for ntfy in the iPhone's Watch app.

To check it, start the server and run:

```
curl -X POST http://localhost:8000/api/test-notification
```

The phone should show 「テスト通知です（IBUKI）」.

Treat the topic like a password, because anyone who knows it can read and send. `.env` is ignored by git, so it won't get committed by accident. If there's no internet the notification fails, but the LED and the dashboard warning still work.

### For the Brain

Put a class called `Brain` in `pc/ibuki/brain.py` and the server picks it up automatically. You can also point to another class with `--brain module:Class`.

```python
class Brain:
    def feed(self, line: str) -> list[dict]:
        ...  # called for every board line; return [] most of the time
```

About 5 to 10 times a second, return a status:

```
{"t": 123456, "state": "OK", "bpm": 34, "disp_mm": [0.12, 0.15], "dt_ms": 60,
 "motion": 12.5, "present": true, "chest_cm": 48.75}
```

`state` is one of EMPTY, OK, STILL, CHECK or ALERT. `bpm` is None when you don't know it. `disp_mm` holds only the new samples since the last status.

When something is wrong, return an event like `{"type": "alert", "reason": "no_breath", "since_s": 10}`. The server then turns the LED red and sends the notification, and the LED goes off again when the state leaves ALERT. The board's button and the dashboard's cancel both arrive as a normal `B,1` line. If `feed()` throws, the server logs it and keeps going.

A few things about the data. D lines come every 60 ms (16.6 per second, not 20). After an `S` line that says `radar restart`, or when `t_ms` jumps by more than half a second, start the phase tracking over. When the board is shaking, treat breathing as unknown and never as "no breathing". The full data format is in [firmware/README.md](../firmware/README.md).

### For the dashboard

The server sends three kinds of messages on `ws://<laptop>:8000/ws`. Brain statuses look like the example above, and the placeholder ones also have `"source": "placeholder"` and `"shaking"`. While the board is shaking, `disp_mm` is empty. Alert events look like `{"type": "alert", ...}`. And `{"type": "board", "connected": true}` (or `false`) comes when you connect and whenever the USB board drops out or comes back.

Use `dt_ms` to place the samples: sample `i` of `n` belongs at `t - (n - 1 - i) * dt_ms`. Send `{"type": "button"}` to cancel an alert, the same as pressing the board's button.

If you build the dashboard (`npm run build` in `web/`), the server also serves it at http://<laptop-ip>:8000, so phones on the same Wi-Fi can open it.

### Recordings we need

The easiest way is the guided session. It goes through the whole list below, waits for Enter before each one, and tells you out loud when to start, hold your breath, tap the table or walk away:

```
../env/bin/python pc/record_session.py
```

Or record them one at a time, 60 seconds each:

```
../env/bin/python pc/record.py <name> --seconds 60
```

Add `--server` if the server is running.

- `empty`: nobody within 1.2 m of the board
- `breathing_20cm`: chest 20 cm from the board, sitting still and breathing normally
- `breathing_30cm` and `breathing_50cm`: the same at 30 and 50 cm
- `hold`: breathe for 20 s, hold your breath for 20 s, then breathe for 20 s
- `moving`: shift around and move your arms
- `shake`: breathe quietly, then tap or bump the table a few times
- `leave`: breathe for 20 s, then walk away

The files go into `recordings/` and git ignores them. To share one, copy it to `recordings/<name>.sample.csv`.

### If something doesn't work

`board_connected` is false: check the cable. If it's plugged in, another program probably has the port, so close the viewer or start it with `--server`.

The log says "team Brain not found, using the PLACEHOLDER": that's normal until `pc/ibuki/brain.py` exists.

No notification: check that `/api/health` shows `"ntfy": true`, that the app is subscribed to the right topic, and that the laptop is online.

## 日本語

IBUKI のノート PC 側です。[ibuki/server.py](ibuki/server.py) が USB でレーダーのボードを読み、すべての行を Brain に渡して、その結果を WebSocket でダッシュボードに送ります。警告のときはボードの赤 LED をつけて、スマホに通知も送ります。

### 動かし方

リポジトリのフォルダで：

```
../env/bin/python pc/ibuki/server.py
```

http://localhost:8000/api/health を開いて、`board_connected` が `true` になっていれば大丈夫です。ボードが手元にないときは、録画を再生できます：

```
../env/bin/python pc/ibuki/server.py --replay recordings/<ファイル>.csv --loop
```

シリアルポートは 1 つのプログラムしか開けません。サーバーを動かしているあいだは、ライブ表示と録画に `--server` を付けて、サーバー経由で読みます：

```
../env/bin/python firmware/tools/live_view.py --server
../env/bin/python pc/record.py breathing_50cm --seconds 60 --server
```

### 中身

[ibuki/serial_reader.py](ibuki/serial_reader.py) はボードの行を読み取り、抜けた行を数えます。USB が抜けても自分でつなぎ直します。

[ibuki/placeholder_brain.py](ibuki/placeholder_brain.py) は、本物の Brain ができるまでの仮の処理です。呼吸の波形、動き、人がいるかどうかは出しますが、呼吸数は出さず、警告も出しません。

[ibuki/shake.py](ibuki/shake.py) はボード自体が動いたことに気づく処理です。ふだん壁や家具は動かないので、それらの反射が一斉に動いたら、動いたのはボードだと判断します。

[record.py](record.py) はボードの生データを `recordings/rec_<名前>_<時刻>.csv` に保存します。

[tests/](tests/) にはテストが 19 個あり、実際にサーバーを起動するものも含まれます：

```
../env/bin/python -m unittest discover -s pc/tests -t pc
```

### 通知

ntfy を使っています。iPhone に ntfy アプリを入れて、+ を押し、リポジトリの `.env` にあるトピック（`IBUKI_NTFY_TOPIC=...` の行）を購読してください。Apple Watch にも届くようにするには、iPhone の Watch アプリで ntfy の通知のミラーリングをオンにします。

確かめるときは、サーバーを起動して：

```
curl -X POST http://localhost:8000/api/test-notification
```

スマホに「テスト通知です（IBUKI）」が届けば OK です。

トピックはパスワードのようなものです。知っている人なら誰でも読めて送れます。`.env` は git の対象外なので、うっかりコミットされることはありません。インターネットがないと通知は届きませんが、LED とダッシュボードの警告はそのまま動きます。

### Brain の担当の人へ

`pc/ibuki/brain.py` に `Brain` というクラスを置くと、サーバーが自動で使います。別のクラスを使うときは `--brain モジュール:クラス` で指定できます。

```python
class Brain:
    def feed(self, line: str) -> list[dict]:
        ...  # ボードの行ごとに呼ばれる。ほとんどは [] を返す
```

1 秒に 5〜10 回くらい、状態を返してください：

```
{"t": 123456, "state": "OK", "bpm": 34, "disp_mm": [0.12, 0.15], "dt_ms": 60,
 "motion": 12.5, "present": true, "chest_cm": 48.75}
```

`state` は EMPTY、OK、STILL、CHECK、ALERT のどれかです。呼吸数がわからないときは `bpm` を None にします。`disp_mm` には前回の状態から増えたサンプルだけを入れます。

何かおかしいときは `{"type": "alert", "reason": "no_breath", "since_s": 10}` のようなイベントを返してください。サーバーが LED を赤にして通知を送り、状態が ALERT でなくなると LED を消します。ボードのボタンとダッシュボードの取り消しは、どちらも普通の `B,1` の行として届きます。`feed()` の中で例外が出ても、サーバーはログに残して動き続けます。

データについて少し。D 行は 60 ms ごと（1 秒に 16.6 本、20 本ではありません）に来ます。`radar restart` と書かれた `S` 行が来たときや、`t_ms` が 0.5 秒以上飛んだときは、位相の追跡をやり直してください。ボードが揺れているあいだは呼吸を「不明」として扱い、「呼吸なし」とは判断しないでください。データの形は [firmware/README.md](../firmware/README.md) に全部書いてあります。

### ダッシュボードの担当の人へ

サーバーは `ws://<PC>:8000/ws` に 3 種類のメッセージを送ります。Brain の状態は上の例と同じ形で、仮の Brain のものには `"source": "placeholder"` と `"shaking"` も付きます。ボードが揺れているあいだ `disp_mm` は空です。警告のイベントは `{"type": "alert", ...}` です。`{"type": "board", "connected": true}`（または `false`）は、接続したときと、USB のボードが外れたり戻ったりしたときに届きます。

サンプルの時刻は `dt_ms` で決めてください。`n` 個のうち `i` 番目は `t - (n - 1 - i) * dt_ms` です。`{"type": "button"}` を送ると、ボードのボタンを押したのと同じく警告を取り消せます。

ダッシュボードをビルドしておけば（`web/` で `npm run build`）、サーバーが http://<PCのIP>:8000 でも表示するので、同じ Wi-Fi のスマホから開けます。

### 必要な録画

いちばん簡単なのはガイド付きの録画です。下のリストを順番に進め、毎回 Enter を待ってから始めます。始める・息を止める・机をたたく・離れるタイミングは、Mac が声で教えてくれます：

```
../env/bin/python pc/record_session.py
```

1 つずつ録る場合は、それぞれ 60 秒ずつ：

```
../env/bin/python pc/record.py <名前> --seconds 60
```

サーバーを動かしているときは `--server` を付けてください。

- `empty`：ボードから 1.2 m 以内に誰もいない
- `breathing_20cm`：胸をボードから 20 cm にして、じっと座って普通に呼吸する
- `breathing_30cm`、`breathing_50cm`：同じことを 30 cm と 50 cm で
- `hold`：20 秒呼吸して、20 秒息を止めて、また 20 秒呼吸する
- `moving`：体を動かしたり、腕を動かしたりする
- `shake`：静かに呼吸して、途中で机を何回かたたく・揺らす
- `leave`：20 秒呼吸してから、その場を離れる

ファイルは `recordings/` に保存され、git には入りません。共有したいときは `recordings/<名前>.sample.csv` にコピーしてください。

### うまく動かないとき

`board_connected` が false：ケーブルを確認してください。つながっているなら、ほかのプログラムがポートを使っているはずです。ライブ表示を閉じるか、`--server` を付けて起動してください。

ログに「team Brain not found, using the PLACEHOLDER」と出る：`pc/ibuki/brain.py` ができるまでは正常です。

通知が来ない：`/api/health` で `"ntfy": true` になっているか、アプリが正しいトピックを購読しているか、PC がインターネットにつながっているかを確認してください。
