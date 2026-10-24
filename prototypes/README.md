# Prototypes

[English](#english) | [日本語](#日本語)

## English

Early experiments from the start of the hackathon. They helped us shape the system, but the running system doesn't use them. The real Brain is [pc/ibuki/brain.py](../pc/ibuki/brain.py), and the real tests are in [pc/tests/](../pc/tests/).

| File | What it is |
|---|---|
| [brain.py](brain.py) | The first Brain prototype. It reads board lines from stdin and uses the PC clock and an adult breathing filter (0.15-0.6 Hz). |
| [fake_board_for_generation.py](fake_board_for_generation.py) | A fake board in the real line format (`C,15.0,37.5,24,10`, 16.6 lines a second). It runs for 5 minutes and switches between 30 s of breathing (15 a minute) and 30 s of breath-hold. |
| [fake_board.py](fake_board.py) | The very first fake board, still in an older format (3 slots, 20 lines a second). |
| [.history-memo/](.history-memo/) | Notes and prompts from building the prototypes. |

To try the fake board with the server, save its output and replay it:

```
../env/bin/python prototypes/fake_board_for_generation.py > recordings/fake_board.csv
../env/bin/python pc/ibuki/server.py --replay recordings/fake_board.csv --loop
```

The first command takes 5 minutes. The fake data has no noise, so don't use it to set thresholds. Use real recordings for that (see [pc/README.md](../pc/README.md)).

## 日本語

ハッカソンの最初に作った試作品です。システムの形を決めるのに役立ちましたが、いま動いているシステムでは使っていません。本物の Brain は [pc/ibuki/brain.py](../pc/ibuki/brain.py)、本物のテストは [pc/tests/](../pc/tests/) にあります。

| ファイル | 内容 |
|---|---|
| [brain.py](brain.py) | 最初の Brain の試作。ボードの行を標準入力から読み、PC の時計と大人用の呼吸フィルタ（0.15〜0.6 Hz）を使います。 |
| [fake_board_for_generation.py](fake_board_for_generation.py) | 本物と同じ形式（`C,15.0,37.5,24,10`、1 秒に 16.6 行）の偽のボード。5 分間動き、30 秒の呼吸（1 分に 15 回）と 30 秒の息止めを交互に出します。 |
| [fake_board.py](fake_board.py) | いちばん最初の偽のボード。古い形式（3 区間、1 秒に 20 行）のままです。 |
| [.history-memo/](.history-memo/) | 試作を作ったときのメモとプロンプト。 |

偽のボードをサーバーで試すときは、出力を保存してから再生します：

```
../env/bin/python prototypes/fake_board_for_generation.py > recordings/fake_board.csv
../env/bin/python pc/ibuki/server.py --replay recordings/fake_board.csv --loop
```

1 行目は 5 分かかります。偽のデータにはノイズがないので、しきい値を決めるのには使わないでください。しきい値には本物の録画を使います（[pc/README.md](../pc/README.md) を参照）。
