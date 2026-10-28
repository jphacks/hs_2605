# proj_cm55/ibuki：IBUKI のファームウェア（Cortex-M55）

レーダーの生データから、PC に送る行（C / D / S / B）を作る部分です。レーダーの設定と読み出しは Infineon のサンプルの [main.c](../main.c) が行い、1 フレームごとに `ibuki_on_frame()` を呼びます。行の形式は [firmware/README.md](../../README.md) にあります。

| ファイル | 内容 |
|---|---|
| [ibuki.h](ibuki.h) / [ibuki.c](ibuki.c) | main.c から呼ぶ部分：起動、1 フレームの処理、メインループでの処理、状態の行（S）。レーダーの設定値（ビンの範囲、1 行に平均するフレーム数など） |
| [range.h](range.h) / [range.c](range.c) | 距離の FFT：チャープごとに直流成分を引き、ハン窓をかけ、64 点の実数 FFT。使う 24 個のビンを足し合わせます（CMSIS-DSP） |
| [lines.h](lines.h) / [lines.c](lines.c) | PC への行。キューに入れて、UART のハードウェア FIFO に空きがあるときに送るので、CPU が UART を待ちません |
| [board_io.h](board_io.h) / [board_io.c](board_io.c) | 警告 LED（赤・緑・青を同時に点滅）、ユーザーボタン（`B,1` を送る）、PC からのコマンド（`L,1` / `L,0` / `R,1` / `T,<ms>`） |
| [load.h](load.h) / [load.c](load.c) | CPU がどれだけ忙しいか。1 分ごとに `S,<時刻>,load dsp 1.5% out 0.0% sleep 95.3% 7158/s queue 246 dropped 0` を送ります |
| [ai.h](ai.h) / [ai.c](ai.c) | 学習済みの 2 つのモデルを AI チップ（Ethos-U55）で動かし、警告まで決めます。Y の行を送り、警告のときは LED を自分で点滅させます。アプリと同じ計算で、同じ録画で同じ判定になりました |
| [sensors.h](sensors.h) / [sensors.c](sensors.c) | ボードの I2C のセンサー。起動時に応答するアドレスを報告し（0x18、0x44、0x68、0x77）、10 秒ごとに温湿度（0x44、SHT4x）を H の行で送ります。動きセンサー（0x68、BMI270、Infineon の sensor-motion-bmi270）で、ボードがぶつかったり持ち上げられたりすると `S,<時刻>,board moved <mg> mg` を送ります（レーダーがベッドを向いていないかもしれないため） |

## 1 フレームの流れ

1. main.c がレーダーの FIFO を読み、1 フレーム（32 チャープ × 64 サンプル × 3 アンテナ）を `ibuki_on_frame()` に渡します
2. `range_add_frame()`：アンテナ 1 本分の各チャープを FFT し、ビンを足します
3. 2 フレームごとに、平均して D の行を作り、キューに入れます（毎秒約 16.6 行）
4. メインループの `ibuki_poll()` が、キューの中身を UART に送り、ボタンとコマンドを見ます

## 測った CPU の使用率（2026-10-27、ボード上）

| | 計算（dsp） | 行の作成と送信（out） |
|---|---|---|
| 以前（行を送り終わるまで待つ） | 1.5% | 15.7% |
| 今（キュー） | 1.5% | 0.0%（行の欠落 0） |

何もすることがないとき（レーダーのデータ待ち、SPI の転送待ち）は、次の割り込みまで CPU を眠らせます（Infineon の `Cy_SysPm_CpuEnterSleep`）。以前は 98.5% の時間、何もせずにループしていました。今は **95% の時間眠っています**（1 秒に約 7,000 回、割り込みで起きます）。

Helium（M55 のベクトル命令）版の CMSIS-DSP は、この GCC 14 ではコンパイルできないため使っていません（理由は [../Makefile](../Makefile) に書いてあります）。計算は CPU の 1.5% です。

## 確かめ方

ボードを USB でつなぎ、ほかのプログラムがポートを使っていない状態で：

```sh
../env/bin/python firmware/tools/recovery_test.py   # リポジトリのフォルダで。再起動と FIFO あふれからの復帰
```

---

# proj_cm55/ibuki: IBUKI's firmware (Cortex-M55)

This turns raw radar data into the lines sent to the PC (C / D / S / B). Infineon's example code in [main.c](../main.c) sets up and reads the radar, and calls `ibuki_on_frame()` for every frame. The line formats are in [firmware/README.md](../../README.md).

| File | What it does |
|---|---|
| [ibuki.h](ibuki.h) / [ibuki.c](ibuki.c) | What main.c calls: start-up, one frame, the main-loop poll, status lines (S). The radar settings (bin range, frames averaged per line, ...) |
| [range.h](range.h) / [range.c](range.c) | Range FFT: for each chirp, remove DC, apply a Hann window, 64-point real FFT, and sum the 24 bins we use (CMSIS-DSP) |
| [lines.h](lines.h) / [lines.c](lines.c) | Lines to the PC, through a queue that is emptied into the UART's hardware FIFO whenever it has room, so the CPU never waits for the UART |
| [board_io.h](board_io.h) / [board_io.c](board_io.c) | Alert LED (blinks red, green and blue together), user button (sends `B,1`), commands from the PC (`L,1` / `L,0` / `R,1` / `T,<ms>`) |
| [load.h](load.h) / [load.c](load.c) | How busy the CPU is: `S,<time>,load dsp 1.5% out 0.0% sleep 95.3% 7158/s queue 246 dropped 0` once a minute |
| [ai.h](ai.h) / [ai.c](ai.c) | Runs the two trained models on the AI chip (Ethos-U55) and decides the alarm: sends Y lines and blinks the LED by itself on an alarm. The same maths as the app; on the same recording it gave the same decisions |
| [sensors.h](sensors.h) / [sensors.c](sensors.c) | The board's I2C sensors: reports at start-up which addresses answer (0x18, 0x44, 0x68, 0x77) and sends the temperature/humidity (0x44, an SHT4x) every 10 s as H lines. With the motion sensor (0x68, a BMI270, through Infineon's sensor-motion-bmi270) it sends `S,<time>,board moved <mg> mg` when the board is bumped, lifted or tilted, since the radar may then no longer face the bed |

## One frame

1. main.c reads the radar FIFO and passes one frame (32 chirps × 64 samples × 3 antennas) to `ibuki_on_frame()`
2. `range_add_frame()` runs the FFT on each chirp of one antenna and adds up the bins
3. Every 2 frames, the sums are averaged into a D line and queued (about 16.6 lines per second)
4. `ibuki_poll()` in the main loop sends queued bytes to the UART and checks the button and commands

## Measured CPU use (2026-10-27, on the board)

| | Maths (dsp) | Making and sending lines (out) |
|---|---|---|
| Before (waiting for each line to be sent) | 1.5% | 15.7% |
| Now (queue) | 1.5% | 0.0% (0 lines lost) |

When there is nothing to do (waiting for radar data or for an SPI transfer), the CPU sleeps until the next interrupt (Infineon's `Cy_SysPm_CpuEnterSleep`). Before, it spun in the loop doing nothing 98.5% of the time. Now it **sleeps 95% of the time** (woken about 7,000 times a second by interrupts).

The Helium (the M55's vector instructions) version of CMSIS-DSP doesn't compile with this GCC 14, so it isn't used (the reason is in [../Makefile](../Makefile)). The maths takes 1.5% of the CPU.

## How to check

With the board on USB and nothing else using its port:

```sh
../env/bin/python firmware/tools/recovery_test.py   # from the repo folder: restart and FIFO-overflow recovery
```
