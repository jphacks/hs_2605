# ml：赤ちゃんの呼吸データと AI モデル

赤ちゃんで「呼吸が止まる」場面を録ることはできません。そこでこのフォルダでは、**実際の赤ちゃんの呼吸のタイミング**と、**私たちのボードで録った実際の部屋の録画**を組み合わせて、赤ちゃんに近いデータを作ります。そのデータで、距離ごとに「いま呼吸しているか」を判定する小さな AI モデルを学習し、今の Brain と同じ条件で比べます。

まだ開発中です。サーバー（[pc/](../pc/README.md)）が使っているのは今も Brain のルールで、このモデルはまだ組み込んでいません。

## ファイル

| ファイル | 内容 |
|---|---|
| [air400.py](air400.py) | 公開データセット AIR-400（実際の赤ちゃん 10 人の夜間の動画で、呼吸ごとに印が付いています）から、呼吸のタイミングだけを取り出します。動画そのものは使いません |
| [synth.py](synth.py) | 実際の録画に、呼吸する赤ちゃんの胸の反射をシミュレーションで足します。ファームウェアと同じ窓関数と FFT で計算するので、ボードが送るデータと同じ形になります。呼吸が止まる場面も入れます |
| [evaluate.py](evaluate.py) | 今の Brain をそのデータで動かして採点します（止まった場面をいくつ見つけたか、誤報は 1 時間に何回か、など） |
| [features.py](features.py) | モデルが見るもの：距離ごとの 12 秒間の、反射の動き |
| [train.py](train.py) | モデル（小さな 1D CNN、重み約 1 万個）の学習と、ボードの AI チップ用の 8 ビット版の書き出し |
| [decide.py](decide.py) | モデルの上のアラームのルールと、Brain との比較 |

作られる赤ちゃんは 2 種類です：

- **alone**：ボードの前に赤ちゃんだけ（ベースは誰もいない部屋の録画）
- **cosleep**：赤ちゃんの横で大人が寝ている（ベースは大人が呼吸している、または息を止めている実際の録画。赤ちゃんは大人よりボードに 10〜20 cm 近い位置）

## 使い方

リポジトリのフォルダで実行します。環境は 2 つあります：

- `../env`（Python 3.14、サーバーと同じ）：`air400` / `synth` / `evaluate` 用。`h5py` が必要です
- `../ml-env`（Python 3.11 + TensorFlow）：`train` / `decide` 用。TensorFlow がまだ Python 3.14 に対応していないため分けています

```sh
python3.11 -m venv ../ml-env && ../ml-env/bin/pip install -r ml/requirements.txt   # 初回だけ
../env/bin/pip install h5py                                                         # 初回だけ

../env/bin/python -m ml.air400     # AIR-400 の呼吸のタイミング（初回はダウンロード）
# 学習用と試験用は、別の赤ちゃん・別の部屋と人で作ります（試験はモデルが見たことのないデータ）
../env/bin/python -m ml.synth --out train --people ramadhan lemonwasp --subjects S01 S02 S03 S04 S05 S06 S07 S08 --per-base 30 --seed 11
../env/bin/python -m ml.synth --out test --people manami live --subjects S09 S10 --per-base 10 --seed 23
../env/bin/python -m ml.synth      # evaluate.py が使うセット（ml/data/synth）
../env/bin/python -m ml.evaluate   # 今の Brain の採点
../ml-env/bin/python -m ml.train   # モデルの学習と 8 ビット版の書き出し
../ml-env/bin/python -m ml.decide  # モデル + ルールと Brain を、試験用のデータで比較
```

作ったデータはダッシュボードで再生できます（サイレンなし、スマートフォンへの通知なし）：

```sh
IBUKI_SIREN=0 ./ibuki.command --replay ml/data/synth/alone/<ファイル名>.csv --env-file ""
```

## 今の状況

- 大人が横で寝ている場面（cosleep）で、今の Brain はほとんどの場合、赤ちゃんではなく大人の呼吸を見ていて、赤ちゃんの呼吸が止まっても気づきません。モデルは赤ちゃんと大人を分けて、止まった場面を見つけられています。
- 背景に動きがある部屋（扇風機やカーテンなど）では、まだ誤報が多すぎます。学習に使った部屋はどれも静かだったためです。そういう部屋の録画を追加して改善中です。
- 結果の数字は、ルールの調整が終わってから試験用のデータで 1 回だけ測り、ここに書きます。

## 注意

- 赤ちゃんの胸の反射の強さ（同じ距離の大人の 0.2〜0.5 倍）と呼吸の深さ（0.5〜2.0 mm）は仮定です。仮定は [synth.py](synth.py) の先頭に書いてあり、実際の赤ちゃんや呼吸のダミーで確かめる予定です。
- AIR-400 のデータにはライセンスの記載がありません。そのため、ダウンロードしたデータと作ったデータ（`ml/data/`）は git に入れません。公開で使う前に作者に確認します。論文：Song et al., "Overcoming Small Data Limitations in Video-Based Infant Respiration Estimation", WACV 2026（[arXiv](https://arxiv.org/abs/2512.06888)、[データ](https://coe.northeastern.edu/Research/AClab/AIR-400/)）
- 医療機器ではありません。

---

# ml: baby breathing data and the AI model

We can't record a baby that stops breathing. So this folder combines **real babies' breathing timing** with **real recordings of our room made by our board** to build baby-like data. On that data it trains a small AI model that says, for each distance, "is something breathing here right now?", and compares it with the current Brain under the same conditions.

This is work in progress. The server ([pc/](../pc/README.md)) still uses the Brain's rules; the model isn't built in yet.

## Files

| File | What it does |
|---|---|
| [air400.py](air400.py) | Takes only the breath timing from AIR-400, a public dataset of overnight videos of 10 real babies with every breath marked. The videos themselves aren't used |
| [synth.py](synth.py) | Adds a simulated breathing baby's chest echo to a real recording, computed with the same window and FFT as the firmware, so it looks exactly like what the board sends. Includes breathing stops |
| [evaluate.py](evaluate.py) | Runs the current Brain on that data and scores it (stops caught, false alarms per hour, ...) |
| [features.py](features.py) | What the model sees: 12 s of echo movement per distance |
| [train.py](train.py) | Trains the model (a small 1D CNN, about 10,000 weights) and writes an 8-bit version for the board's AI chip |
| [decide.py](decide.py) | The alarm rules on top of the model, and the comparison with the Brain |

Two kinds of simulated session:

- **alone**: only the baby in front of the board (based on recordings of an empty room)
- **cosleep**: an adult sleeping beside the baby (based on real recordings of an adult breathing or holding their breath; the baby is 10-20 cm nearer the board than the adult)

## How to run

From the repo folder. There are two environments:

- `../env` (Python 3.14, the same as the server): for `air400`, `synth`, `evaluate`. Needs `h5py`
- `../ml-env` (Python 3.11 + TensorFlow): for `train`, `decide`. Separate because TensorFlow doesn't support Python 3.14 yet

```sh
python3.11 -m venv ../ml-env && ../ml-env/bin/pip install -r ml/requirements.txt   # once
../env/bin/pip install h5py                                                         # once

../env/bin/python -m ml.air400     # AIR-400 breath timing (downloads the first time)
# training and test sets use different babies and different rooms/people (the test is unseen)
../env/bin/python -m ml.synth --out train --people ramadhan lemonwasp --subjects S01 S02 S03 S04 S05 S06 S07 S08 --per-base 30 --seed 11
../env/bin/python -m ml.synth --out test --people manami live --subjects S09 S10 --per-base 10 --seed 23
../env/bin/python -m ml.synth      # the set evaluate.py uses (ml/data/synth)
../env/bin/python -m ml.evaluate   # score the current Brain
../ml-env/bin/python -m ml.train   # train the model, write the 8-bit version
../ml-env/bin/python -m ml.decide  # model + rules vs the Brain, on the test set
```

The generated sessions replay in the dashboard (no siren, no phone notifications):

```sh
IBUKI_SIREN=0 ./ibuki.command --replay ml/data/synth/alone/<file>.csv --env-file ""
```

## Status

- With an adult sleeping beside the baby (cosleep), the current Brain mostly watches the adult's breathing instead of the baby's, and misses the baby's stops. The model separates baby and adult and catches the stops.
- In rooms with background movement (a fan, a curtain), false alarms are still far too frequent, because all the training rooms were still. We are adding recordings of such rooms.
- The numbers will be measured once on the test set after the rules are settled, and written here.

## Notes

- The baby's echo strength (0.2-0.5 times an adult's at the same distance) and breath depth (0.5-2.0 mm) are assumptions, listed at the top of [synth.py](synth.py), to be checked with real babies or a breathing dummy.
- AIR-400's data has no stated license, so the downloaded and generated data (`ml/data/`) is never committed. We'll ask the authors before any public use. Paper: Song et al., "Overcoming Small Data Limitations in Video-Based Infant Respiration Estimation", WACV 2026 ([arXiv](https://arxiv.org/abs/2512.06888), [data](https://coe.northeastern.edu/Research/AClab/AIR-400/))
- Not a medical device.
