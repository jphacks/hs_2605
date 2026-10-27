# ml：赤ちゃんの呼吸データと AI モデル

赤ちゃんで「呼吸が止まる」場面を録ることはできません。そこでこのフォルダでは、**実際の赤ちゃんの呼吸のタイミング**と、**私たちのボードで録った実際の部屋の録画**を組み合わせて、赤ちゃんに近いデータを作ります。そのデータで 2 つの小さな AI モデルを学習し、今の Brain と同じ条件で比べます：

1. **呼吸モデル**：距離ごとに「いま呼吸しているか」を判定します（12 秒分の反射の動きから）
2. **状態モデル**：呼吸モデルの 30 秒分の答えから、赤ちゃんがいま「呼吸している・止まった・動いている・いない」のどれかを決めます。手で書いたアラームのルールの代わりです。残るルールは「止まった」が設定の秒数続いたら警告、の 1 つだけです

サーバー（[pc/](../pc/README.md)）は、モデルのファイルがあれば、このモデルで判定します。

## ファイル

| ファイル | 内容 |
|---|---|
| [air400.py](air400.py) | 公開データセット AIR-400（実際の赤ちゃん 10 人の夜間の動画で、呼吸ごとに印が付いています）から、呼吸のタイミングだけを取り出します。動画そのものは使いません |
| [synth.py](synth.py) | 実際の録画に、呼吸する赤ちゃんの胸の反射をシミュレーションで足します。ファームウェアと同じ窓関数と FFT で計算するので、ボードが送るデータと同じ形になります。呼吸が止まる場面も入れます |
| [evaluate.py](evaluate.py) | 今の Brain をそのデータで動かして採点します（止まった場面をいくつ見つけたか、誤報は 1 時間に何回か、など） |
| [features.py](features.py) | モデルが見るもの：距離ごとの 12 秒間の、反射の動き |
| [train.py](train.py) | 呼吸モデル（小さな 1D CNN、重み約 1 万個）の学習と、ボードの AI チップ用の 8 ビット版の書き出し |
| [state_model.py](state_model.py) | 状態モデル（2D CNN、重み 22,492 個、8 ビット版 30 KB）の学習と採点。シミュレーションの停止と、意味が分かっている実際の録画（息止め、離れる、誰もいない、動いている、横で作業している人）で学習します |
| [decide.py](decide.py) | 最初に試した手書きのアラームのルールと、Brain との比較（比べるために残しています） |
| [antennas.py](antennas.py) | 3 本のアンテナで録った録画（E の行）から、呼吸している胸の向きを求めます。左右に座った同じ人で、+32° と −31° と反対の向きになりました（2026-10-28） |
| [board_models.py](board_models.py) | 2 つのモデルを Arm の Vela でボードの AI チップ（Ethos-U55）向けに変換し、ファームウェア用の C の配列を作ります（git には入れません） |

作られる赤ちゃんは 2 種類です：

- **alone**：ボードの前に赤ちゃんだけ（ベースは誰もいない部屋の録画）
- **cosleep**：赤ちゃんの横で大人が寝ている（ベースは大人が呼吸している、または息を止めている実際の録画。赤ちゃんは大人よりボードに 10〜20 cm 近い位置）

## 使い方

リポジトリのフォルダで実行します。環境は 2 つあります：

- `../env`（Python 3.14、サーバーと同じ）：`air400` / `synth` / `evaluate` 用。`h5py` が必要です
- `../ml-env`（Python 3.11 + TensorFlow）：`train` / `state_model` / `decide` 用。TensorFlow がまだ Python 3.14 に対応していないため分けています

```sh
python3.11 -m venv ../ml-env && ../ml-env/bin/pip install -r ml/requirements.txt   # 初回だけ
../env/bin/pip install h5py                                                         # 初回だけ

../env/bin/python -m ml.air400     # AIR-400 の呼吸のタイミング（初回はダウンロード）
# 学習用・検証用・試験用は別の赤ちゃんで作り、試験用はさらに別の部屋と人で作ります。
# 設定は検証用で決め、試験用（モデルが見たことのないデータ）は最後に 1 回だけ使います
../env/bin/python -m ml.synth --out train --people ramadhan lemonwasp --subjects S01 S02 S03 S04 S05 S06 --per-base 30 --seed 11
../env/bin/python -m ml.synth --out val --people ramadhan lemonwasp --subjects S07 S08 --per-base 10 --seed 31
../env/bin/python -m ml.synth --out test --people manami live --subjects S09 S10 --per-base 10 --seed 23
../env/bin/python -m ml.synth      # evaluate.py が使うセット（ml/data/synth）
../env/bin/python -m ml.evaluate   # 今の Brain の採点
../ml-env/bin/python -m ml.train   # 呼吸モデルの学習と 8 ビット版の書き出し
../ml-env/bin/python -m ml.state_model          # 状態モデルの学習、検証用データでの採点、8 ビット版の書き出し
../ml-env/bin/python -m ml.state_model --test   # 最後に 1 回だけ：試験用データと、学習に使っていない実際の録画
../ml-env/bin/python -m ml.decide  # 手書きのルールと Brain を、試験用のデータで比較
# 実際の録画で誤報を数える（録画中ずっと誰もが呼吸しているので、警告はすべて誤報）
../ml-env/bin/python -m ml.decide --real recordings/room/<ファイル名>.csv
```

作ったデータはダッシュボードで再生できます（サイレンなし、スマートフォンへの通知なし）：

```sh
IBUKI_SIREN=0 ./ibuki.command --replay ml/data/synth/alone/<ファイル名>.csv --env-file ""
```

## 結果（試験用データ、1 回だけ測定）

試験用データは、学習にも設定の調整にも使っていない赤ちゃん（AIR-400 の S09・S10）と、別の部屋・人（Manami の録画と、夜のテストの部屋）で作りました。夜のテストの部屋は、誰もいなくても何かが揺れている部屋です。「見つけた停止 / 見つけられるはずの停止」、1 時間あたりの誤報、警告までの時間（中央値）：

| 場面 | 設定 | Brain（今のルール） | 状態モデル |
|---|---|---|---|
| 赤ちゃんだけ | 10 秒 | 5/10、誤報 4.7 回、16.4 秒 | **10/10、誤報 0、12.1 秒** |
| 赤ちゃんだけ | 20 秒 | 4/9、誤報 2.4 回、26.4 秒 | **7/9、誤報 0、22.1 秒** |
| 大人の横で寝ている | 10 秒 | 0/20 | **20/20、誤報 0、12.0 秒** |
| 大人の横で寝ている | 20 秒 | 0/16 | **16/16、誤報 0、22.1 秒** |

学習に使っていない実際の録画：

| 録画 | Brain | 状態モデル |
|---|---|---|
| ボードの横で人が作業（20 分） | 誤報 0 | 誤報 0 |
| Manami の息止め（50 cm、20 秒） | 気づかない | 10 秒の設定で警告（止めてから約 15 秒） |
| Manami が離れる | 「いない」 | 「いない」 |
| 誰もいない・動いている・机を叩く | 誤報 0 | 誤報 0 |
| Manami のとても浅い呼吸（20・30・50 cm、各 1 分） | 誤報 0（※） | 誤報 4 回（10 秒）、1 回（20 秒） |

※ Brain の設定は、この録画を見て決めたものです。

最初に試した手書きのルール（[decide.py](decide.py)）は、人が作業している同じ 20 分で誤報 5 回、Manami が離れる場面でも誤報を出しました。

わかっている弱点：

- とても浅い呼吸で誤報が出ます。
- 警告は設定の秒数より約 2 秒遅れます。誤報を半分にするために選んだ設定です（検証用データで決めました）。

## 注意

- 赤ちゃんの胸の反射の強さ（同じ距離の大人の 0.2〜0.5 倍）と呼吸の深さ（0.5〜2.0 mm）は仮定です。仮定は [synth.py](synth.py) の先頭に書いてあります。
- AIR-400 のデータにはライセンスの記載がありません。そのため、ダウンロードしたデータと作ったデータ（`ml/data/`）は git に入れません。論文：Song et al., "Overcoming Small Data Limitations in Video-Based Infant Respiration Estimation", WACV 2026（[arXiv](https://arxiv.org/abs/2512.06888)、[データ](https://coe.northeastern.edu/Research/AClab/AIR-400/)）
- 医療機器ではありません。

---

# ml: baby breathing data and the AI model

We can't record a baby that stops breathing. So this folder combines **real babies' breathing timing** with **real recordings of our room made by our board** to build baby-like data. On that data it trains two small AI models and compares them with the current Brain under the same conditions:

1. **Breathing model**: for each distance, "is something breathing here right now?" (from 12 s of echo movement)
2. **State model**: from the breathing model's last 30 s of answers, is the baby breathing, stopped, moving, or not there? It replaces hand-written alarm rules. The one rule left: "stopped" for the set number of seconds means an alert

The server ([pc/](../pc/README.md)) decides with these models whenever their files are there.

## Files

| File | What it does |
|---|---|
| [air400.py](air400.py) | Takes only the breath timing from AIR-400, a public dataset of overnight videos of 10 real babies with every breath marked. The videos themselves aren't used |
| [synth.py](synth.py) | Adds a simulated breathing baby's chest echo to a real recording, computed with the same window and FFT as the firmware, so it looks exactly like what the board sends. Includes breathing stops |
| [evaluate.py](evaluate.py) | Runs the current Brain on that data and scores it (stops caught, false alarms per hour, ...) |
| [features.py](features.py) | What the model sees: 12 s of echo movement per distance |
| [train.py](train.py) | Trains the breathing model (a small 1D CNN, about 10,000 weights) and writes an 8-bit version for the board's AI chip |
| [state_model.py](state_model.py) | Trains and scores the state model (a 2D CNN, 22,492 weights, 30 KB in 8 bits), on simulated stops and on real recordings whose meaning is known (breath holds, leaving, empty room, moving, a person working beside the board) |
| [decide.py](decide.py) | The hand-written alarm rules we tried first, and the comparison with the Brain (kept for comparison) |
| [antennas.py](antennas.py) | The direction of the breathing chest, from recordings with all 3 antennas (E lines). The same person sitting left and then right of the board came out at +32° and −31° (2026-10-28) |
| [board_models.py](board_models.py) | Compiles both models with Arm's Vela for the board's AI chip (Ethos-U55) and writes them as C arrays for the firmware (not in git) |

Two kinds of simulated session:

- **alone**: only the baby in front of the board (based on recordings of an empty room)
- **cosleep**: an adult sleeping beside the baby (based on real recordings of an adult breathing or holding their breath; the baby is 10-20 cm nearer the board than the adult)

## How to run

From the repo folder. There are two environments:

- `../env` (Python 3.14, the same as the server): for `air400`, `synth`, `evaluate`. Needs `h5py`
- `../ml-env` (Python 3.11 + TensorFlow): for `train`, `state_model`, `decide`. Separate because TensorFlow doesn't support Python 3.14 yet

```sh
python3.11 -m venv ../ml-env && ../ml-env/bin/pip install -r ml/requirements.txt   # once
../env/bin/pip install h5py                                                         # once

../env/bin/python -m ml.air400     # AIR-400 breath timing (downloads the first time)
# training, validation and test use different babies; the test also uses other rooms and people.
# Settings are chosen on validation; the test (never seen by the models) is used once, at the end
../env/bin/python -m ml.synth --out train --people ramadhan lemonwasp --subjects S01 S02 S03 S04 S05 S06 --per-base 30 --seed 11
../env/bin/python -m ml.synth --out val --people ramadhan lemonwasp --subjects S07 S08 --per-base 10 --seed 31
../env/bin/python -m ml.synth --out test --people manami live --subjects S09 S10 --per-base 10 --seed 23
../env/bin/python -m ml.synth      # the set evaluate.py uses (ml/data/synth)
../env/bin/python -m ml.evaluate   # score the current Brain
../ml-env/bin/python -m ml.train   # train the breathing model, write the 8-bit version
../ml-env/bin/python -m ml.state_model          # train the state model, score on validation, write the 8-bit version
../ml-env/bin/python -m ml.state_model --test   # once, at the end: the test set and real recordings it never trained on
../ml-env/bin/python -m ml.decide  # the hand-written rules vs the Brain, on the test set
# count false alarms on a real recording (everyone keeps breathing, so every alarm is false)
../ml-env/bin/python -m ml.decide --real recordings/room/<file>.csv
```

The generated sessions replay in the dashboard (no siren, no phone notifications):

```sh
IBUKI_SIREN=0 ./ibuki.command --replay ml/data/synth/alone/<file>.csv --env-file ""
```

## Results (test set, measured once)

The test set uses babies that were used neither for training nor for choosing settings (AIR-400 S09 and S10), in other rooms with other people (Manami's recordings and the room from the night test, where something keeps moving even when nobody is there). Stops caught / stops that could be caught, false alarms per hour, median time to the alert:

| Situation | Setting | Brain (today's rules) | State model |
|---|---|---|---|
| Baby alone | 10 s | 5/10, 4.7 false/h, 16.4 s | **10/10, 0 false, 12.1 s** |
| Baby alone | 20 s | 4/9, 2.4 false/h, 26.4 s | **7/9, 0 false, 22.1 s** |
| Beside a sleeping adult | 10 s | 0/20 | **20/20, 0 false, 12.0 s** |
| Beside a sleeping adult | 20 s | 0/16 | **16/16, 0 false, 22.1 s** |

Real recordings never used for training:

| Recording | Brain | State model |
|---|---|---|
| A person working beside the board (20 min) | 0 false alarms | 0 false alarms |
| Manami holds her breath (50 cm, 20 s) | missed | alert with the 10 s setting (~15 s into the hold) |
| Manami walks away | "not there" | "not there" |
| Empty room, moving, bumping the table | 0 false alarms | 0 false alarms |
| Manami's very light breathing (20, 30, 50 cm, 1 min each) | 0 false alarms (*) | 4 false alarms (10 s), 1 (20 s) |

(*) The Brain's settings were chosen by looking at these recordings.

The hand-written rules we tried first ([decide.py](decide.py)) gave 5 false alarms in the same 20 minutes of a person working, and a false alarm when Manami walked away.

Known weak points:

- Very light breathing causes false alarms.
- The alert comes about 2 s after the setting. That setting halves the false alarms (chosen on the validation set).

## Notes

- The baby's echo strength (0.2-0.5 times an adult's at the same distance) and breath depth (0.5-2.0 mm) are assumptions, listed at the top of [synth.py](synth.py).
- AIR-400's data has no stated license, so the downloaded and generated data (`ml/data/`) is never committed. Paper: Song et al., "Overcoming Small Data Limitations in Video-Based Infant Respiration Estimation", WACV 2026 ([arXiv](https://arxiv.org/abs/2512.06888), [data](https://coe.northeastern.edu/Research/AClab/AIR-400/))
- Not a medical device.
