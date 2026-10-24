# IBUKI firmware (KIT_PSE84_AI)

[English](#english) | [日本語](#日本語)

---

## English

Firmware for the Infineon PSOC Edge E84 AI Kit. The 60 GHz radar (XENSIV BGT60TR13C) measures a baby in a crib. The board does the per-chirp range FFT and streams complex range bins to the PC over the KitProg3 USB serial port. All breathing and motion analysis happens on the PC. Not a medical device.

Based on Infineon's `mtb-example-psoc-edge-ml-deepcraft-deploy-radar` (original README: [README_infineon.md](README_infineon.md)). The gesture model is replaced by our code in [proj_cm55/ibuki.c](proj_cm55/ibuki.c). Changes to the example are in [proj_cm55/main.c](proj_cm55/main.c).

### What the board does

Every radar frame, using RX antenna 1:

1. For each of the 32 chirps, subtract the chirp's mean (DC) and apply a Hann window.
2. Run a 64-point real FFT (CMSIS-DSP `arm_rfft_fast_f32`).
3. Average the complex bins over the chirps.
4. Average 2 frames, then send range bins 15–101 cm as one `D` line.

### Radar settings (preset P1)

| Setting | Value |
|---|---|
| Frequency | 58.5–62.5 GHz (4 GHz sweep) |
| Samples per chirp | 64 at 2 MHz |
| Chirps per frame | 32 |
| RX antennas | 3 read, RX1 used |
| Frame rate | 33.3 frames/s (30.04 ms) |
| Range bin | 3.75 cm |
| Max range | 1.2 m (32 bins × 3.75 cm) |
| Range gate sent | bins 4–27 = 15.0–101.25 cm |

The register values come from Infineon's tool ([proj_cm55/radar_settings.h](proj_cm55/radar_settings.h)). Do not edit them by hand.

### Line protocol (115200 8N1, one message per line, no spaces)

| Line | Direction | Meaning |
|---|---|---|
| `C,first_cm,step_mm,n,scale` | board → PC | At boot and every 5 s. Now `C,15.0,37.5,24,10`. |
| `D,seq,t_ms,I0,Q0,…,In-1,Qn-1` | board → PC | ~16.6 lines/s. `seq` +1 per D line, `t_ms` = board ms since boot, bins nearest first. Value = integer / `scale`. |
| `S,t_ms,text` | board → PC | Status and errors, for example `S,0,ibuki fw start`. |
| `B,1` | board → PC | User button pressed. |
| `L,1` / `L,0` | PC → board | Red LED on / off. |

Ignore any other line.

### Tools (macOS, Apple Silicon)

- ModusToolbox tools 3.7 or newer (tested with 3.9), in `/Applications/ModusToolbox`
- Arm GCC 14.2.1 (`mtbgccpackage`)
- ModusToolbox Edge Protect Security Suite
- ModusToolbox Programming Tools (OpenOCD), needed for `make program`

The installers are on the Infineon Developer Center. Downloading them in a browser is much faster than from the command line.

### Build, flash, check

```
cd firmware
make getlibs          # first time on a machine: downloads libraries into ../mtb_shared
make build -j8        # Debug build. GCC fails in Release mode (see Infineon README)
make program          # flashes over KitProg3. This replaces the out-of-box demo
```

Check the stream from the repo root:

```
source ../env/bin/activate
python firmware/tools/check_stream.py --seconds 20 --out range.png
```

Expected: about 16.6 D lines/s and `gaps: 0`. The plot shows the mean magnitude per range bin, and below it how much each bin changes over time. A breathing or moving person shows up in the lower plot.

Measured on 2026-10-24: 16.64 D lines/s, no seq gaps in 336 lines, 3.8 KB/s on the UART (a third of the limit).

### Known issues

- Bins under ~25 cm contain strong antenna leakage. Keep the baby farther than ~20 cm from the board, or ignore those bins on the PC.
- `make program` warns that the KitProg3 firmware is out of date. Flashing still works.
- The repo-root `.gitignore` must ignore `mtb_shared/` (240 MB of libraries).

### Troubleshooting

- **No serial port:** check `ls /dev/cu.usbmodem*`, use the KitProg3 USB-C port and a data cable, and close other serial programs.
- **No D lines:** look for `S` lines. Radar driver errors are sent as `S,t_ms,<what> 0x<code>`.
- **Seq gaps:** lines are lost. Send fewer bins or average more frames (`IBUKI_NUM_BINS`, `IBUKI_FRAMES_PER_LINE` in [proj_cm55/ibuki.h](proj_cm55/ibuki.h)).

---

## 日本語

Infineon PSOC Edge E84 AI Kit 用のファームウェアです。60 GHz レーダー（XENSIV BGT60TR13C）でベビーベッドの赤ちゃんを計測します。ボード上でチャープごとのレンジ FFT を行い、複素レンジビンを KitProg3 の USB シリアルで PC に送ります。呼吸や動きの解析はすべて PC 側で行います。医療機器ではありません。

Infineon のサンプル `mtb-example-psoc-edge-ml-deepcraft-deploy-radar` をもとにしています（元の README：[README_infineon.md](README_infineon.md)）。ジェスチャーモデルを [proj_cm55/ibuki.c](proj_cm55/ibuki.c) の処理に置き換えました。サンプルからの変更点は [proj_cm55/main.c](proj_cm55/main.c) にあります。

### ボードでの処理

レーダーの 1 フレームごとに、RX アンテナ 1 のデータで：

1. 32 本のチャープそれぞれで平均値（DC）を引き、ハン窓をかける。
2. 64 点の実数 FFT（CMSIS-DSP `arm_rfft_fast_f32`）を行う。
3. 複素ビンをチャープ方向に平均する。
4. 2 フレームを平均し、15〜101 cm のレンジビンを 1 本の `D` 行として送る。

### レーダー設定（プリセット P1）

| 項目 | 値 |
|---|---|
| 周波数 | 58.5〜62.5 GHz（掃引幅 4 GHz） |
| チャープあたりサンプル数 | 64（2 MHz） |
| フレームあたりチャープ数 | 32 |
| RX アンテナ | 3 本読み出し、RX1 を使用 |
| フレームレート | 33.3 フレーム/秒（30.04 ms） |
| レンジビン幅 | 3.75 cm |
| 最大距離 | 1.2 m（32 ビン × 3.75 cm） |
| 送信するレンジ | ビン 4〜27 = 15.0〜101.25 cm |

レジスタ値は Infineon のツールで生成したものです（[proj_cm55/radar_settings.h](proj_cm55/radar_settings.h)）。手で編集しないでください。

### 通信プロトコル（115200 8N1、1 行 1 メッセージ、空白なし）

| 行 | 方向 | 意味 |
|---|---|---|
| `C,first_cm,step_mm,n,scale` | ボード → PC | 起動時と 5 秒ごと。現在は `C,15.0,37.5,24,10`。 |
| `D,seq,t_ms,I0,Q0,…,In-1,Qn-1` | ボード → PC | 約 16.6 行/秒。`seq` は D 行ごとに +1、`t_ms` は起動からのミリ秒、ビンは近い順。値 = 整数 / `scale`。 |
| `S,t_ms,text` | ボード → PC | 状態・エラー。例：`S,0,ibuki fw start`。 |
| `B,1` | ボード → PC | ユーザーボタンが押された。 |
| `L,1` / `L,0` | PC → ボード | 赤 LED の点灯 / 消灯。 |

それ以外の行は無視してください。

### 必要なツール（macOS、Apple Silicon）

- ModusToolbox tools 3.7 以降（3.9 で確認）、`/Applications/ModusToolbox` に配置
- Arm GCC 14.2.1（`mtbgccpackage`）
- ModusToolbox Edge Protect Security Suite
- ModusToolbox Programming Tools（OpenOCD）：`make program` に必要

インストーラーは Infineon Developer Center にあります。コマンドラインよりブラウザでダウンロードする方がずっと速いです。

### ビルド・書き込み・確認

```
cd firmware
make getlibs          # 新しいマシンで最初の 1 回：../mtb_shared にライブラリを取得
make build -j8        # Debug ビルド。GCC の Release ビルドは失敗します（Infineon README 参照）
make program          # KitProg3 で書き込み。出荷時デモは上書きされます
```

リポジトリのルートでストリームを確認：

```
source ../env/bin/activate
python firmware/tools/check_stream.py --seconds 20 --out range.png
```

期待される結果：D 行が約 16.6 行/秒、`gaps: 0`。グラフの上段はレンジビンごとの平均振幅、下段は各ビンの時間変化の大きさです。呼吸や動きのある人は下段に現れます。

2026-10-24 の測定結果：D 行 16.64 行/秒、336 行で seq の欠落なし、UART 使用量 3.8 KB/秒（上限の約 3 分の 1）。

### 既知の問題

- 約 25 cm より近いビンにはアンテナの強い漏れ込みがあります。赤ちゃんはボードから 20 cm 以上離すか、PC 側でそのビンを無視してください。
- `make program` で KitProg3 のファームウェアが古いという警告が出ますが、書き込みは成功します。
- リポジトリ直下の `.gitignore` で `mtb_shared/`（ライブラリ 240 MB）を除外する必要があります。

### トラブルシューティング

- **シリアルポートがない：** `ls /dev/cu.usbmodem*` を確認。KitProg3 側の USB-C ポートとデータ通信対応ケーブルを使い、他のシリアルソフトを閉じてください。
- **D 行が来ない：** `S` 行を確認してください。レーダードライバのエラーは `S,t_ms,<内容> 0x<コード>` として送られます。
- **seq が飛ぶ：** 行が欠落しています。ビン数を減らすか、平均するフレーム数を増やしてください（[proj_cm55/ibuki.h](proj_cm55/ibuki.h) の `IBUKI_NUM_BINS`、`IBUKI_FRAMES_PER_LINE`）。
