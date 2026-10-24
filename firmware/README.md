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
| `R,1` | PC → board | Restart the radar (stop, clear the FIFO, start). |
| `T,<ms>` | PC → board | Test only: stall the board up to 2000 ms to check the recovery. |

Ignore any other line.

### Reliability

The board recovers by itself and reports it as an `S` line:

| Problem | What the board does | S line |
|---|---|---|
| Sensor FIFO overflow (data could be shifted) | Drops that block and restarts the radar | `radar restart: fifo overflow 0x00000000` |
| No frame for 1 s | Watchdog restarts the radar | `radar restart: no frames for 1 s 0x00000000` |
| PC sent `R,1` | Restarts the radar | `radar restart: requested 0x00000000` |

The last number is the driver status (0 = OK). After a restart, `t_ms` jumps forward while `seq` keeps counting. The PC should restart phase tracking when it sees a `radar restart` line or a `t_ms` step over 0.5 s.

Test it on the board (nothing else may use the port):

```
../env/bin/python firmware/tools/recovery_test.py
```

Measured on 2026-10-24: all four phases passed, with no seq gaps and data continuing after every restart.

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

Other tools in [tools/](tools/): `live_view.py` (live plots of movement, breathing wave, signal quality and board shakes) and `recovery_test.py` (see Reliability).

### Known issues

- Bins under ~28 cm contain the radar's own TX→RX leakage. They are still sent, because a child in a bed can be close to the board. The PC tools ignore them for picking the target by default (`--min-cm`). Whether a child closer than ~25 cm stands out above the leakage is not tested yet.
- If `make program` warns that the KitProg3 firmware is out of date, update it once (takes about a minute; done on our board on 2026-10-24, 2.80 → 2.90):
  ```
  /Applications/ModusToolboxProgtools-2.0/fw-loader/bin/fw-loader --update-kp3 all
  ```
- VS Code shows false "undefined identifier" errors in `main.c` because it does not know the ModusToolbox include paths. The real build is clean. `make vscode` in `firmware/` sets the paths up.
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
| `R,1` | PC → ボード | レーダーを再起動（停止・FIFO クリア・再開）。 |
| `T,<ms>` | PC → ボード | テスト専用：復帰処理の確認のため、ボードを最大 2000 ms 止める。 |

それ以外の行は無視してください。

### 信頼性

ボードは自動で復帰し、`S` 行で知らせます：

| 問題 | ボードの動作 | S 行 |
|---|---|---|
| センサー FIFO のオーバーフロー（データがずれる可能性） | そのブロックを捨ててレーダーを再起動 | `radar restart: fifo overflow 0x00000000` |
| 1 秒間フレームが来ない | ウォッチドッグがレーダーを再起動 | `radar restart: no frames for 1 s 0x00000000` |
| PC から `R,1` | レーダーを再起動 | `radar restart: requested 0x00000000` |

最後の数値はドライバの状態です（0 = 正常）。再起動のあと `t_ms` は先に飛びますが、`seq` は続けて増えます。PC 側は `radar restart` の行、または 0.5 秒を超える `t_ms` の飛びを見たら位相の追跡をやり直してください。

ボードでのテスト（他のプログラムがポートを使っていないこと）：

```
../env/bin/python firmware/tools/recovery_test.py
```

2026-10-24 の測定結果：4 つの段階すべて合格。seq の欠落なし、再起動のたびにデータが再開。

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

[tools/](tools/) にはほかに `live_view.py`（動き・呼吸の波形・信号品質・ボードの揺れのライブ表示）と `recovery_test.py`（「信頼性」参照）があります。

### 既知の問題

- 約 28 cm より近いビンには、レーダー自身の送信→受信の漏れ込みがあります。ベッドの子どもがボードの近くにいることもあるため、このビンも送信しています。PC のツールは標準では対象の選択にこのビンを使いません（`--min-cm`）。約 25 cm より近い子どもが漏れ込みより強く見えるかは、まだ確認していません。
- `make program` で KitProg3 のファームウェアが古いという警告が出たら、一度だけ更新してください（約 1 分。私たちのボードは 2026-10-24 に 2.80 → 2.90 に更新済み）：
  ```
  /Applications/ModusToolboxProgtools-2.0/fw-loader/bin/fw-loader --update-kp3 all
  ```
- VS Code では `main.c` に「undefined identifier」という誤ったエラーが表示されます。ModusToolbox のインクルードパスを知らないためで、実際のビルドは問題ありません。`firmware/` で `make vscode` を実行すると設定されます。
- リポジトリ直下の `.gitignore` で `mtb_shared/`（ライブラリ 240 MB）を除外する必要があります。

### トラブルシューティング

- **シリアルポートがない：** `ls /dev/cu.usbmodem*` を確認。KitProg3 側の USB-C ポートとデータ通信対応ケーブルを使い、他のシリアルソフトを閉じてください。
- **D 行が来ない：** `S` 行を確認してください。レーダードライバのエラーは `S,t_ms,<内容> 0x<コード>` として送られます。
- **seq が飛ぶ：** 行が欠落しています。ビン数を減らすか、平均するフレーム数を増やしてください（[proj_cm55/ibuki.h](proj_cm55/ibuki.h) の `IBUKI_NUM_BINS`、`IBUKI_FRAMES_PER_LINE`）。
