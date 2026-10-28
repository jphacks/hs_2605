# 息吹～IBUKI～ firmware

[日本語](#日本語) | [English](#english)

## 日本語

Infineon PSOC Edge E84 AI Kit（KIT_PSE84_AI）用のファームウェアです。60 GHz レーダーを読み、ボードの上でレンジ FFT まで計算して、その結果を KitProg3 の USB シリアルでノート PC に送ります。呼吸や動きの判定、警告などはすべて PC 側で行います。

Infineon のサンプル `mtb-example-psoc-edge-ml-deepcraft-deploy-radar` をもとに、ジェスチャーのモデルを自分たちの処理（[proj_cm55/ibuki/](proj_cm55/ibuki/README.md)）に置き換えました。Infineon の元の README は [README_infineon.md](README_infineon.md) にあります。

### ボードでやっていること

レーダーの 1 フレームごとに、RX アンテナ 1 のデータを使います。32 本のチャープそれぞれで DC を取り除き、ハン窓をかけて、CMSIS-DSP で 64 点の FFT をします。複素数のビンをチャープ方向と 2 フレーム分で平均して、15〜101 cm のビンを 1 本の `D` 行として送ります。1 秒に約 16.6 行で、115200 baud の 3 分の 1 くらいしか使いません。

### レーダーの設定

[proj_cm55/radar_settings.h](proj_cm55/radar_settings.h) のプリセット P1 を使っています。レジスタの値は Infineon のツールで作ったものなので、手で書き換えないでください。

| 項目 | 値 |
|---|---|
| 周波数 | 58.5〜62.5 GHz |
| チャープあたりのサンプル数 | 64（2 MHz） |
| フレームあたりのチャープ数 | 32 |
| RX アンテナ | 3 本読んで、RX1 を使用 |
| フレームレート | 1 秒に 33.3 回 |
| 距離の分解能 | 3.75 cm |
| 最大距離 | 1.2 m |
| 送っているビン | 4〜27（15.0〜101.25 cm） |

### シリアルの通信

115200 8N1、1 行に 1 つのメッセージ、カンマ区切りで空白なし。形が合わない行は無視してください。

| 行 | 向き | 意味 |
|---|---|---|
| `C,first_cm,step_mm,n,scale` | ボードから PC | 起動したときと 5 秒ごと。いまは `C,15.0,37.5,24,10`。 |
| `D,seq,t_ms,I0,Q0,…,In-1,Qn-1` | ボードから PC | データ本体。`seq` は 1 行ごとに 1 ずつ増え、`t_ms` は起動からの時間、ビンは近い順、値は整数を `scale` で割ったもの。 |
| `E,seq,t_ms,<base64>` | ボードから PC | `M,3` のあとだけ：3 本のアンテナすべて。base64 の中身は int16（リトルエンディアン）を、アンテナ・ビン・実部/虚部の順に 3×24×2 個。値は D の行と同じ平均ですが `scale` をかけません。読み方は [ml/antennas.py](../ml/antennas.py) |
| `Y,t_ms,state,event` | ボードから PC | ボードの AI チップ（Ethos-U55）で動く 2 つのモデルの判定（約 0.5 秒ごと）。`state` は breathing / stopped / moving / empty、`event` は `-` / `no_breath` / `left_bed`。警告のときはボードが自分で LED を点滅させ、SW1 で止まります。モデルは `../ml-env/bin/python -m ml.board_models` で作ります（なければ Y の行は出ません） |
| `H,t_ms,temp_c,humidity` | ボードから PC | 10 秒ごと、ボードの上の温湿度センサー（I2C 0x44、SHT4x）。センサーはレーダーとプロセッサーの横にあるので、部屋より高い「ボードの温度」です（25 °C ほどの部屋で 36 °C） |
| `S,t_ms,text` | ボードから PC | 状態やエラー。例：`S,0,ibuki fw start`。1 分ごとに CPU の使用率も送ります：`S,60000,load dsp 1.5% out 0.0% sleep 95.3% 7158/s queue 246 dropped 0` |
| `B,1` | ボードから PC | ユーザーボタンが押された |
| `L,1` / `L,0` | PC からボード | 警告：RGB LED が赤・緑・青を同時につけて 1 秒に 2 回点滅（ボードの電源などの赤いランプはいつも点いているため、赤だけでは目立ちませんでした）、または消灯 |
| `R,1` | PC からボード | レーダーを再起動する |
| `M,1` / `M,3` | PC からボード | アンテナ 1 本を D の行で送る（起動時はこれ）／3 本すべてを E の行で送る。録画は `pc/record.py <名前> --antennas 3` |
| `T,<ms>` | PC からボード | テスト用。ボードを最大 2000 ms 止める |

### レーダーに問題が起きたとき

センサーの FIFO があふれると、読んだデータがずれている可能性があるので、そのブロックは捨ててレーダーを再起動します。1 秒間フレームが来ないときも、ウォッチドッグが再起動します。どちらの場合も `S,74268,radar restart: fifo overflow 0x00000000` のような `S` 行を送ります。最後の数字はドライバの状態で、0 なら再起動は成功です。

再起動のあとは `t_ms` が先に飛びますが、`seq` はそのまま続きます。PC 側は、この行を見たときか、`t_ms` が 0.5 秒以上飛んだときに、位相の追跡をやり直してください。

[tools/recovery_test.py](tools/recovery_test.py) で、実機を使ってこれを確かめられます（再起動の指示、300 ms の停止、1.5 秒の停止）。私たちのボードではすべて通っています。

### ビルドと書き込み

ModusToolbox 3.7 以降（私たちは 3.9）、Arm GCC 14.2.1、Edge Protect Security Suite、Programming Tools（OpenOCD が入っています）が必要です。どれも Infineon の Developer Center にあります。ターミナルからよりブラウザでダウンロードしたほうがずっと速かったです。

```
cd firmware
make getlibs     # マシンごとに最初の 1 回。../mtb_shared に約 240 MB ダウンロード
make build -j8   # Debug のみ。このサンプルは GCC で Release ビルドができない
make program     # KitProg3 で書き込み。ボードに最初から入っているデモは消えます
```

書き込んだら、リポジトリのフォルダでデータを確認します：

```
../env/bin/python firmware/tools/check_stream.py --seconds 20
```

D 行が 1 秒に約 16.6 本、`gaps: 0` なら大丈夫です。データをリアルタイムで見るには [tools/live_view.py](tools/live_view.py) を使います。どこで動きがあるか、呼吸の波形、信号の品質、ボードが揺れていないかがわかります。

### 知っておくこと

約 28 cm より近いビンには、レーダー自身の送信信号が受信側に漏れ込んでいます。ベッドの子どもはボードの近くにいることもあるので送ってはいますが、PC のツールは対象を探すときにこのビンを使いません。約 25 cm より近い子どもが漏れ込みより強く見えるかは、まだ試していません。

`make program` で KitProg3 のファームウェアが古いと言われたら、一度だけ更新してください：

```
/Applications/ModusToolboxProgtools-2.0/fw-loader/bin/fw-loader --update-kp3 all
```

VS Code では `main.c` の一部が「undefined」と赤く表示されますが、ModusToolbox のインクルードパスを知らないだけで、ビルドは問題ありません。このフォルダで `make vscode` を実行すると設定されます。

### うまく動かないとき

シリアルポートが見つからない：`ls /dev/cu.usbmodem*` を確認してください。KitProg3 側の USB-C ポートに、データ通信ができるケーブルでつなぎ、ポートを使っている他のソフトは閉じてください。

D 行が来ない：`S` 行を見てください。ドライバのエラーはエラーコード付きでそこに出ます。

`seq` が飛ぶ：途中で行が失われています。[proj_cm55/ibuki/ibuki.h](proj_cm55/ibuki/ibuki.h) の `IBUKI_NUM_BINS` を減らすか、`IBUKI_FRAMES_PER_LINE` を増やしてください。

## English

This is the firmware for the Infineon PSOC Edge E84 AI Kit (KIT_PSE84_AI). It reads the 60 GHz radar, does the range FFT on the board, and streams the result to the laptop over the KitProg3 USB serial port. Everything after that, like breathing, movement and alerts, happens on the PC.

We started from Infineon's `mtb-example-psoc-edge-ml-deepcraft-deploy-radar` and replaced their gesture model with our own processing in [proj_cm55/ibuki/](proj_cm55/ibuki/README.md). Infineon's original README is in [README_infineon.md](README_infineon.md).

### What the board does

For every radar frame we take RX antenna 1. For each of the 32 chirps we remove the DC offset, apply a Hann window and run a 64-point FFT with CMSIS-DSP. The complex bins are averaged over the chirps and over two frames, and the bins from 15 cm to 101 cm go out as one `D` line. That's about 16.6 lines a second, which uses roughly a third of the 115200 baud link.

### Radar settings

We use preset P1 from [proj_cm55/radar_settings.h](proj_cm55/radar_settings.h). The register values come from Infineon's tool, so don't edit them by hand.

| Setting | Value |
|---|---|
| Frequency | 58.5–62.5 GHz |
| Samples per chirp | 64 at 2 MHz |
| Chirps per frame | 32 |
| RX antennas | 3 read, RX1 used |
| Frame rate | 33.3 per second |
| Range bin | 3.75 cm |
| Max range | 1.2 m |
| Bins we send | 4 to 27, which is 15.0 to 101.25 cm |

### Serial protocol

115200 8N1, one message per line, comma separated, no spaces. Ignore any line that doesn't match.

| Line | Direction | What it means |
|---|---|---|
| `C,first_cm,step_mm,n,scale` | board to PC | Sent at boot and every 5 s. Right now it's `C,15.0,37.5,24,10`. |
| `D,seq,t_ms,I0,Q0,…,In-1,Qn-1` | board to PC | The data. `seq` goes up by one per line, `t_ms` is the board's time since boot, bins are nearest first, and each value is the integer divided by `scale`. |
| `E,seq,t_ms,<base64>` | board to PC | Only after `M,3`: all 3 antennas. The base64 holds int16 values (little-endian), antenna by antenna, bin by bin, Re then Im: 3×24×2 values, averaged like the D line but without `scale`. Read with [ml/antennas.py](../ml/antennas.py). |
| `Y,t_ms,state,event` | board to PC | The decision of the two models running on the board's AI chip (Ethos-U55), about every 0.5 s. `state` is breathing / stopped / moving / empty, `event` is `-` / `no_breath` / `left_bed`. On an alarm the board blinks its LED by itself; SW1 stops it. Build the models with `../ml-env/bin/python -m ml.board_models` (without them there are no Y lines). |
| `H,t_ms,temp_c,humidity` | board to PC | Every 10 s, from the temperature/humidity sensor on the board (I2C 0x44, an SHT4x). It sits beside the radar and the processor, so it reads the board's temperature, warmer than the room (36 °C in a ~25 °C room). |
| `S,t_ms,text` | board to PC | Status and errors, for example `S,0,ibuki fw start`. Once a minute also the CPU use: `S,60000,load dsp 1.5% out 0.0% sleep 95.3% 7158/s queue 246 dropped 0`. |
| `B,1` | board to PC | The user button was pressed. |
| `L,1` / `L,0` | PC to board | Alert: the RGB LED blinks twice a second with red, green and blue on together (the board's power and other red lights are always on, so plain red was easy to miss), or off. |
| `R,1` | PC to board | Restart the radar. |
| `M,1` / `M,3` | PC to board | Send one antenna as D lines (the default at start-up) / all 3 as E lines. Record with `pc/record.py <name> --antennas 3`. |
| `T,<ms>` | PC to board | For testing only. Stalls the board for up to 2000 ms. |

### When the radar has problems

If the sensor's FIFO overflows, the block we just read might be shifted, so the board throws it away and restarts the radar. If no frame arrives for a second, a watchdog restarts it too. Either way the board sends an `S` line such as `S,74268,radar restart: fifo overflow 0x00000000`. The last number is the driver status, and 0 means the restart worked.

After a restart, `t_ms` jumps ahead but `seq` keeps counting. The PC should start its phase tracking over when it sees one of these lines, or a jump in `t_ms` of more than half a second.

[tools/recovery_test.py](tools/recovery_test.py) checks all of this on a real board with a restart request, a 300 ms stall and a 1.5 s stall. It passes on ours.

### Building and flashing

You need ModusToolbox 3.7 or newer (we use 3.9), the Arm GCC 14.2.1 package, the Edge Protect Security Suite and the Programming Tools, which bring OpenOCD. They're all on Infineon's Developer Center. Downloading them in the browser was much faster for us than from the terminal.

```
cd firmware
make getlibs     # once per machine, downloads about 240 MB into ../mtb_shared
make build -j8   # Debug only, GCC can't build Release for this example
make program     # flashes over KitProg3 and replaces the demo that ships on the board
```

Then check the stream from the repo folder:

```
../env/bin/python firmware/tools/check_stream.py --seconds 20
```

You should see about 16.6 D lines per second and `gaps: 0`. To watch the data live, run [tools/live_view.py](tools/live_view.py). It shows where something is moving, the breathing wave, the signal quality, and whether the board is being shaken.

### Good to know

The bins closer than about 28 cm contain the radar's own transmit signal leaking into the receiver. We still send them, because a child in bed can be close to the board, but the PC tools skip them when they look for the target. We haven't tested yet whether a child closer than about 25 cm stands out above the leakage.

If `make program` says the KitProg3 firmware is out of date, update it once:

```
/Applications/ModusToolboxProgtools-2.0/fw-loader/bin/fw-loader --update-kp3 all
```

VS Code marks things in `main.c` as undefined because it doesn't know the ModusToolbox include paths. The build itself is fine. Running `make vscode` in this folder sets the paths up.

### If something doesn't work

No serial port: run `ls /dev/cu.usbmodem*`. Use the KitProg3 USB-C port with a cable that carries data, and close anything else that has the port open.

No D lines: look at the `S` lines. Driver errors show up there with their error code.

Gaps in `seq`: lines are getting lost on the way. Send fewer bins or average more frames with `IBUKI_NUM_BINS` and `IBUKI_FRAMES_PER_LINE` in [proj_cm55/ibuki/ibuki.h](proj_cm55/ibuki/ibuki.h).
