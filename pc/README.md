# 息吹～IBUKI～ on the PC

[English](#english) | [日本語](#日本語)

## English

This is the laptop side of 息吹～IBUKI～. [ibuki/server.py](ibuki/server.py) reads the radar board over USB, hands every line to the Brain, and sends the Brain's results to the dashboard over a WebSocket. It also turns the board's red LED on during an alert and sends the notification to the phone.

### Running it

The easiest way is `./ibuki.command` in the repo folder: it starts the server and opens the dashboard. On its first start it also makes a Python environment in `.venv` and installs the packages; the commands below use it. To run only the server:

```
.venv/bin/python pc/ibuki/server.py
```

Then open http://localhost:8000/api/health and check that `board_connected` is `true`. If you don't have the board with you, replay a recording instead:

```
.venv/bin/python pc/ibuki/server.py --replay recordings/<file>.csv --loop
```

Only one program can have the serial port open. While the server runs, start the live viewer and the recorder with `--server` so they read through it:

```
.venv/bin/python firmware/tools/live_view.py --server
.venv/bin/python pc/record.py breathing_50cm --seconds 60 --server
```

### What's in here

[ibuki/serial_reader.py](ibuki/serial_reader.py) parses the board's lines, counts lost lines and reconnects on its own when the USB cable gets pulled.

[ibuki/brain.py](ibuki/brain.py) is the Brain: presence, movement, breathing rate, and the states and alerts described below. The server uses it automatically. The no-breathing alert waits 10 s for demos. For a baby, start the server with `IBUKI_NO_BREATH_S=20`. Its thresholds come from Ramadhan's and lemonwasp's recordings and were checked on Manami's too (24 recordings from 3 people). The comment next to each threshold says what the recordings showed.

[ibuki/placeholder_brain.py](ibuki/placeholder_brain.py) is the simple stand-in from before the Brain existed (`--brain placeholder`). It never gives a breathing rate and never raises an alert.

[ibuki/shake.py](ibuki/shake.py) notices when the board itself is moving. Normally the walls and furniture don't move, so if all of their echoes jump at once, it's the board.

[record.py](record.py) saves the raw board lines to `recordings/rec_<name>_<time>.csv`.

[tests/](tests/) has 44 tests, including ones that start the real server. Run them with:

```
.venv/bin/python -m unittest discover -s pc/tests -t pc
```

### Notifications

We use ntfy. Install the ntfy app on the iPhone, tap +, and subscribe to the topic in the repo's `.env` file (the line `IBUKI_NTFY_TOPIC=...`). To get the notifications on the Apple Watch too, turn on mirroring for ntfy in the iPhone's Watch app.

To check it, start the server and run:

```
curl -X POST http://localhost:8000/api/test-notification
```

The phone should show 「【テスト】息吹～IBUKI～の通知」.

To show the team what every notification looks like, send all four, lowest to highest urgency (no server needed):

```
.venv/bin/python pc/notify_demo.py
.venv/bin/python pc/notify_demo.py no_breath    # only this one
```

Treat the topic like a password, because anyone who knows it can read and send. `.env` is ignored by git, so it won't get committed by accident. If there's no internet the notification fails, but the LED and the dashboard warning still work.

### How the Brain plugs in

The server loads the class `Brain` from `pc/ibuki/brain.py` (another class can be chosen with `--brain module:Class`).

```python
class Brain:
    def feed(self, line: str) -> list[dict]:
        ...  # called for every board line; return [] most of the time
```

About 8 times a second, it returns a status:

```
{"t": 123456, "state": "OK", "bpm": 34, "disp_mm": [0.12, 0.15], "dt_ms": 60,
 "motion": 12.5, "present": true, "chest_cm": 48.75}
```

`bpm` is None when the rate isn't known. `disp_mm` holds only the new samples since the last status. `state` follows this table:

| In bed? | Moving? | Breathing? | state |
|---|---|---|---|
| no | | | EMPTY |
| yes | yes | unknown (movement hides it) | OK (awake) |
| yes | no | yes | STILL (asleep, breathing) |
| yes | no | none found yet | CHECK (counting) |
| yes | no | none for 20 s (10 s for the demo) | ALERT |

It tells "in bed" from "empty" by the echo. When the room goes quiet, it checks whether the person's echo is still there: leaving takes it away (2-6% of their usual echo in our recordings), a breath-hold keeps it (59% or more). It doesn't rely on seeing them move first, because someone can move and then stop breathing. When the board shakes, breathing is unknown: a shake never leads to CHECK or ALERT.

Events go out as extra dicts in the same list:

| Event | When | What the server does |
|---|---|---|
| `{"type": "alert", "reason": "no_breath", "since_s": 20}` | entering ALERT | LED red, urgent push |
| `{"type": "alert", "reason": "left_bed"}` | the baby was in bed and is gone | LED red, high push |
| `{"type": "info", "reason": "fast_breathing", "bpm": 62}` | asleep and above 50 breaths a minute (WHO "fast breathing" at 2 to 12 months) for 5 minutes. One level for every age for now | high push, no LED |
| `{"type": "info", "reason": "woke_up"}` | moving again after 10 minutes asleep (still), at most every 5 minutes | silent push, no LED |

CHECK alone sends no notification: it flickers on during short pauses, and parents who get too many pings start ignoring them. Every notification title starts with its urgency: 【緊急】, 【注意】 or 【お知らせ】.

For no breathing, the laptop also plays a loud siren. Like a baby monitor, it keeps sounding until a person acknowledges it: the red 「アラームを止める」 button on the dashboard, the board's SW1 button, or Enter in the server window. Breathing coming back alone does not stop it. `IBUKI_SIREN=0` in `.env` turns it off.

The LED goes off when someone presses the button. After "left the bed" it also goes off when a status says OK or STILL again (the baby is back). After no breathing it keeps blinking until the button, like the siren. The board's button and the dashboard's cancel both arrive as a normal `B,1` line. If `feed()` throws, the server logs it and keeps going.

A few things about the data. D lines come every 60 ms (16.6 per second). The Brain filters breathing at 0.1 to 1.5 Hz (6 to 90 a minute), which covers adults and babies (about 30 to 60 a minute). Newborns often pause for 5 to 10 s, which is why the alert for a baby waits 20 s. After an `S` line that says `radar restart`, or when `t_ms` jumps by more than half a second, it starts the phase tracking over. The full data format is in [firmware/README.md](../firmware/README.md).

### What the server sends to the dashboard

The server sends four kinds of messages on `ws://<laptop>:8000/ws`. Brain statuses look like the example above, and the placeholder ones also have `"source": "placeholder"` and `"shaking"`. While the board is shaking, `disp_mm` is empty. Alerts are `{"type": "alert", ...}` and info events are `{"type": "info", ...}`, both described in the Brain section. And `{"type": "board", "connected": true}` (or `false`) comes when you connect and whenever the USB board drops out or comes back.

The dashboard shows the states as EMPTY ベッドにいません, OK 起きています, STILL 眠っています, CHECK 呼吸を確認中 and ALERT 呼吸が確認できません, and センサーが揺れています while `shaking` is true.

Sample `i` of `n` belongs at `t - (n - 1 - i) * dt_ms`. A client can send `{"type": "button"}` to cancel an alert, the same as pressing the board's button.

If you build the dashboard (`npm run build` in `web/`), the server also serves it at http://<laptop-ip>:8000, so phones on the same Wi-Fi can open it.

### Recording

The guided session records the set below, one after another. It waits for Enter before each one and tells you out loud when to start, hold your breath, tap the table or walk away:

```
.venv/bin/python pc/record_session.py
```

Or record them one at a time, 60 seconds each:

```
.venv/bin/python pc/record.py <name> --seconds 60
```

Add `--server` if the server is running.

- `empty`: nobody within 1.2 m of the board
- `breathing_20cm`: chest 20 cm from the board, sitting still. Breathe with the voice (10 a minute) for 30 s, then normally
- `breathing_30cm` and `breathing_50cm`: the same at 30 and 50 cm
- `hold`: breathe for 20 s, hold your breath for 20 s, then breathe for 20 s
- `moving`: shift around and move your arms
- `shake`: breathe quietly, then tap or bump the table a few times
- `leave`: breathe for 20 s, then walk away

The session asks for a name and saves to `recordings/<name>/`; three of us recorded this set on Hack Day. Git ignores the recordings, except small samples ending in `.sample.csv` (three are in [recordings/samples/](../recordings/samples/)).

### If something doesn't work

`board_connected` is false: check the cable. If it's plugged in, another program probably has the port, so close the viewer or start it with `--server`.

The log says "Brain ... FAILED to load": `pc/ibuki/brain.py` has an error. The message names it, and the server falls back to the placeholder.

No notification: check that `/api/health` shows `"ntfy": true`, that the app is subscribed to the right topic, and that the laptop is online.

## 日本語

息吹～IBUKI～のノート PC 側です。[ibuki/server.py](ibuki/server.py) が USB でレーダーのボードを読み、すべての行を Brain に渡して、その結果を WebSocket でダッシュボードに送ります。警告のときはボードの赤 LED をつけて、スマホに通知も送ります。

### 動かし方

いちばん簡単なのは、リポジトリのフォルダで `./ibuki.command` を動かすことです。サーバーを起動して、ダッシュボードを開きます。最初の起動では `.venv` に Python の環境を作り、パッケージも入れます。下のコマンドはそれを使います。サーバーだけを動かすときは：

```
.venv/bin/python pc/ibuki/server.py
```

http://localhost:8000/api/health を開いて、`board_connected` が `true` になっていれば大丈夫です。ボードが手元にないときは、録画を再生できます：

```
.venv/bin/python pc/ibuki/server.py --replay recordings/<ファイル>.csv --loop
```

シリアルポートは 1 つのプログラムしか開けません。サーバーを動かしているあいだは、ライブ表示と録画に `--server` を付けて、サーバー経由で読みます：

```
.venv/bin/python firmware/tools/live_view.py --server
.venv/bin/python pc/record.py breathing_50cm --seconds 60 --server
```

### 中身

[ibuki/serial_reader.py](ibuki/serial_reader.py) はボードの行を読み取り、抜けた行を数えます。USB が抜けても自分でつなぎ直します。

[ibuki/brain.py](ibuki/brain.py) が Brain です。在室、動き、呼吸数、下に書いた状態と警告を判定します。サーバーが自動で使います。「呼吸なし」の警告はデモ用に 10 秒で出ます。赤ちゃん向けには `IBUKI_NO_BREATH_S=20` を付けてサーバーを起動してください。しきい値は Ramadhan と lemonwasp の録画から決め、Manami の録画でも確かめました（3 人・24 本）。各しきい値の横のコメントに、録画で何が見えたかを書いてあります。

[ibuki/placeholder_brain.py](ibuki/placeholder_brain.py) は、Brain ができる前の簡単な代わりです（`--brain placeholder`）。呼吸数は出さず、警告も出しません。

[ibuki/shake.py](ibuki/shake.py) はボード自体が動いたことに気づく処理です。ふだん壁や家具は動かないので、それらの反射が一斉に動いたら、動いたのはボードだと判断します。

[record.py](record.py) はボードの生データを `recordings/rec_<名前>_<時刻>.csv` に保存します。

[tests/](tests/) にはテストが 44 個あり、実際にサーバーを起動するものも含まれます：

```
.venv/bin/python -m unittest discover -s pc/tests -t pc
```

### 通知

ntfy を使っています。iPhone に ntfy アプリを入れて、+ を押し、リポジトリの `.env` にあるトピック（`IBUKI_NTFY_TOPIC=...` の行）を購読してください。Apple Watch にも届くようにするには、iPhone の Watch アプリで ntfy の通知のミラーリングをオンにします。

確かめるときは、サーバーを起動して：

```
curl -X POST http://localhost:8000/api/test-notification
```

スマホに「【テスト】息吹～IBUKI～の通知」が届けば OK です。

チームにすべての通知の見た目を見せるときは、緊急度の低い順に 4 つ送ります（サーバーは不要です）：

```
.venv/bin/python pc/notify_demo.py
.venv/bin/python pc/notify_demo.py no_breath    # これだけ送る
```

トピックはパスワードのようなものです。知っている人なら誰でも読めて送れます。`.env` は git の対象外なので、うっかりコミットされることはありません。インターネットがないと通知は届きませんが、LED とダッシュボードの警告はそのまま動きます。

### Brain のつなぎ方

サーバーは `pc/ibuki/brain.py` の `Brain` というクラスを使います（別のクラスは `--brain モジュール:クラス` で指定できます）。

```python
class Brain:
    def feed(self, line: str) -> list[dict]:
        ...  # ボードの行ごとに呼ばれる。ほとんどは [] を返す
```

1 秒に約 8 回、状態を返します：

```
{"t": 123456, "state": "OK", "bpm": 34, "disp_mm": [0.12, 0.15], "dt_ms": 60,
 "motion": 12.5, "present": true, "chest_cm": 48.75}
```

呼吸数がわからないときは `bpm` を None にします。`disp_mm` には前回の状態から増えたサンプルだけを入れます。`state` は次の表のとおりです：

| ベッドにいる？ | 動いている？ | 呼吸 | state |
|---|---|---|---|
| いない | | | EMPTY |
| いる | 動いている | 不明（動きで見えない） | OK（起きている） |
| いる | 静止 | あり | STILL（眠っている・呼吸あり） |
| いる | 静止 | まだ見つからない | CHECK（数えている） |
| いる | 静止 | 20 秒なし（デモは 10 秒） | ALERT |

「ベッドにいる」と「空」は反射で見分けます。静かになったら、その人の反射が残っているかを確かめます。いなくなれば反射は消え（録画ではふだんの 2〜6%）、息を止めているだけなら残ります（59% 以上）。動いたあとに息が止まることもあるので、「先に動いたから、いなくなった」とは決めません。ボードが揺れたときは呼吸を「不明」にし、揺れが原因で CHECK や ALERT にはしません。

イベントは、同じリストに別の dict として入れます：

| イベント | いつ | サーバーの動き |
|---|---|---|
| `{"type": "alert", "reason": "no_breath", "since_s": 20}` | ALERT になったとき | LED を赤、緊急の通知 |
| `{"type": "alert", "reason": "left_bed"}` | ベッドにいた赤ちゃんがいなくなったとき | LED を赤、高い優先度の通知 |
| `{"type": "info", "reason": "fast_breathing", "bpm": 62}` | 眠っていて、1 分に 50 回（WHO の「速い呼吸」の 2〜12 か月の目安）を 5 分超えたとき。今は年齢によらず同じ値です | 高い優先度の通知、LED なし |
| `{"type": "info", "reason": "woke_up"}` | 10 分以上眠って（じっとして）いたあとに動き出したとき。5 分に 1 回まで | 音なしの通知、LED なし |

CHECK だけでは通知を送りません。短い間だけ CHECK になることが多く、通知が多すぎると保護者が見なくなるからです。通知のタイトルは、緊急度（【緊急】【注意】【お知らせ】）から始まります。

呼吸が確認できないときは、パソコンから大きなアラーム音も鳴ります。ベビーモニターと同じく、人が確認するまで鳴り続けます。止めるには、ダッシュボードの赤い「アラームを止める」ボタン、ボードの SW1 ボタン、またはサーバーのウィンドウの Enter を押します。呼吸が戻っただけでは止まりません。`.env` に `IBUKI_SIREN=0` と書くと鳴らなくなります。

ボタンが押されると LED は消えます。「ベッドにいません」のあとは、状態が OK か STILL に戻ったとき（赤ちゃんが戻ったとき）にも消えます。「呼吸が確認できません」のあとは、アラーム音と同じく、ボタンが押されるまで点滅し続けます。ボードのボタンとダッシュボードの取り消しは、どちらも普通の `B,1` の行として届きます。`feed()` の中で例外が出ても、サーバーはログに残して動き続けます。

データについて少し。D 行は 60 ms ごと（1 秒に 16.6 本）に来ます。Brain は呼吸を 0.1〜1.5 Hz（1 分に 6〜90 回）で取り出すので、大人にも赤ちゃん（1 分に 30〜60 回くらい）にも合います。新生児は 5〜10 秒息が止まることがよくあるので、赤ちゃん向けの警告は 20 秒待ちます。`radar restart` と書かれた `S` 行が来たときや、`t_ms` が 0.5 秒以上飛んだときは、位相の追跡をやり直します。データの形は [firmware/README.md](../firmware/README.md) に全部書いてあります。

### ダッシュボードに送るもの

サーバーは `ws://<PC>:8000/ws` に 4 種類のメッセージを送ります。Brain の状態は上の例と同じ形で、仮の Brain のものには `"source": "placeholder"` と `"shaking"` も付きます。ボードが揺れているあいだ `disp_mm` は空です。警告は `{"type": "alert", ...}`、お知らせは `{"type": "info", ...}` で、どちらも Brain の章に書いてあります。`{"type": "board", "connected": true}`（または `false`）は、接続したときと、USB のボードが外れたり戻ったりしたときに届きます。

ダッシュボードは状態を、EMPTY ベッドにいません、OK 起きています、STILL 眠っています、CHECK 呼吸を確認中、ALERT 呼吸が確認できません と表示し、`shaking` が true のあいだは「センサーが揺れています」と表示します。

サンプルの時刻は `dt_ms` で決まります。`n` 個のうち `i` 番目は `t - (n - 1 - i) * dt_ms` です。`{"type": "button"}` を送ると、ボードのボタンを押したのと同じく警告を取り消せます。

ダッシュボードをビルドしておけば（`web/` で `npm run build`）、サーバーが http://<PCのIP>:8000 でも表示するので、同じ Wi-Fi のスマホから開けます。

### 録画のしかた

ガイド付きの録画で、下の一式を順番に録れます。毎回 Enter を待ってから始めます。始める・息を止める・机をたたく・離れるタイミングは、Mac が声で教えてくれます：

```
.venv/bin/python pc/record_session.py
```

1 つずつ録る場合は、それぞれ 60 秒ずつ：

```
.venv/bin/python pc/record.py <名前> --seconds 60
```

サーバーを動かしているときは `--server` を付けてください。

- `empty`：ボードから 1.2 m 以内に誰もいない
- `breathing_20cm`：胸をボードから 20 cm にして、じっと座る。30 秒は声に合わせて呼吸し（1 分に 10 回）、そのあとは普通に呼吸する
- `breathing_30cm`、`breathing_50cm`：同じことを 30 cm と 50 cm で
- `hold`：20 秒呼吸して、20 秒息を止めて、また 20 秒呼吸する
- `moving`：体を動かしたり、腕を動かしたりする
- `shake`：静かに呼吸して、途中で机を何回かたたく・揺らす
- `leave`：20 秒呼吸してから、その場を離れる

最初に名前を聞き、`recordings/<名前>/` に保存します。Hack Day に 3 人がこの一式を録りました。録画は git には入りません。ただし、名前の最後が `.sample.csv` の小さな録画は入ります（[recordings/samples/](../recordings/samples/) に 3 本）。

### うまく動かないとき

`board_connected` が false：ケーブルを確認してください。つながっているなら、ほかのプログラムがポートを使っているはずです。ライブ表示を閉じるか、`--server` を付けて起動してください。

ログに「Brain ... FAILED to load」と出る：`pc/ibuki/brain.py` にエラーがあります。メッセージに理由が出て、サーバーは仮の Brain で動き続けます。

通知が来ない：`/api/health` で `"ntfy": true` になっているか、アプリが正しいトピックを購読しているか、PC がインターネットにつながっているかを確認してください。
