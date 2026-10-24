# IBUKI（いぶき）— カメラを使わない赤ちゃんの呼吸見守り

<!-- TODO: デモ動画・画像ができたら差し替え
[![IBUKI demo](画像URL)](動画URL)
-->

> 医療機器ではありません。病気の診断や SIDS（乳幼児突然死症候群）の予防はできません。

## 製品概要
### 背景(製品開発のきっかけ、課題等）
赤ちゃんが寝ている間、保護者は「ちゃんと息をしているか」が気になり、夜中に何度も様子を見に行きます。
カメラ型のベビーモニターは映像が残ることへのプライバシーの不安があり、体に付けるセンサーは赤ちゃんが嫌がったり外れたりします。

### 製品説明（具体的な製品の説明）
ベビーベッドに取り付けた 60 GHz ミリ波レーダーで、赤ちゃんに触れず、カメラも使わずに、胸のわずかな動きを計測します。
PC で呼吸の波形・呼吸数・体の動きを計算してダッシュボードに表示し、異常が続いたときは保護者のスマートフォンやウェアラブルに通知します（通知は実装済み、スマートフォンでの受信は確認中）。

### 特長
#### 1. カメラ不使用・非接触
映像を撮らないのでプライバシーに配慮できます。赤ちゃんの体には何も付けません。

#### 2. ミリ波レーダーで胸の動きをとらえる
レーダーの反射の位相から胸の変位（mm）を求めます。距離ごとに分けて計測するので、どの距離で動きがあるかもわかります。

#### 3. すべてローカルで動作
計測データはクラウドに送らず、手元の PC で処理します。外部に送るのは通知だけです。

### 解決出来ること
- 夜中に何度も見に行く保護者の負担を減らします。
- 別の部屋にいても、スマートフォンやウェアラブルで様子がわかります。

### 今後の展望
- 3 本の受信アンテナを合成して感度を上げる
- ボードを小さな箱に収め、ベビーベッドに取り付けやすくする
- 赤ちゃんの呼吸データで判定のしきい値を調整する

### 注力したこと（こだわり等）
* レーダーのファームウェアを自作し、チャープごとのレンジ FFT をボード上で実行。PC へ約 16.6 行/秒で欠落なく送信（実機で確認）
* 誤解を与えない表示：呼吸数がわからないときは 0 ではなく「不明」と表示
* 故障に強い作り：ボードが揺れたときは呼吸を「不明」にして誤った警告を出さない。USB の抜き差しやレーダーの停止からも自動で復帰（実機で確認）
* 判定のしきい値は推測ではなく、実際の録画データから決める

## システム構成

```mermaid
flowchart LR
  R["60 GHz レーダー<br>KIT_PSE84_AI"] -- "USB シリアル<br>約 16.6 行/秒" --> S["PC: サーバー + Brain<br>呼吸・動き・状態"]
  S -- "WebSocket" --> W["Web ダッシュボード"]
  S -- "ntfy" --> N["スマートフォン / ウェアラブル"]
  S -- "L,1（警告時に赤 LED）" --> R
```

| 部分 | 状態 |
|---|---|
| レーダーファームウェア（[firmware/](firmware/)） | 完成・実機で確認済み（FIFO オーバーフロー時の自動復帰、ウォッチドッグ付き） |
| サーバー（[pc/](pc/)） | 完成・実機で確認済み（WebSocket、LED、通知、USB の抜き差しから自動復帰） |
| ライブ表示ツール（[firmware/tools/live_view.py](firmware/tools/live_view.py)） | 完成・実機で約 50 cm の人を検出。信号品質とボードの揺れも表示 |
| Web ダッシュボード（[web/](web/)） | モック。サーバーへの接続を作業中 |
| Brain（呼吸数・状態の判定） | 開発中。今は仮の Brain が波形・動き・在室を表示（呼吸数とアラートはなし） |
| スマートフォン・ウェアラブルへの通知 | 実装済み（送信を確認、スマートフォンでの受信は確認中） |

## 動かし方
- **ファームウェア：** ビルド・書き込み・通信プロトコルは [firmware/README.md](firmware/README.md) を参照
- **サーバー：** ボードを USB で接続して（通知の設定などは [pc/README.md](pc/README.md)）
  ```
  ../env/bin/python pc/ibuki/server.py
  ```
- **ライブ表示（レーダーの動作確認）：** サーバー実行中は `--server` を付ける
  ```
  ../env/bin/python firmware/tools/live_view.py --server
  ```
- **ダッシュボード（モック）：**
  ```
  cd web
  npm install
  npm run dev
  ```

## 開発技術
### 活用した技術
#### API・データ
* ntfy（プッシュ通知）
* WebSocket（PC からダッシュボードへの状態配信）

#### フレームワーク・ライブラリ・モジュール
* ModusToolbox 3.9、CMSIS-DSP、XENSIV BGT60TRxx ドライバ（ファームウェア）
* Python：FastAPI、uvicorn、websockets、NumPy、pyserial、matplotlib
* React、TypeScript、Vite、Recharts（ダッシュボード）

#### デバイス
* Infineon PSOC Edge E84 AI Kit（KIT_PSE84_AI）：XENSIV BGT60TR13C 60 GHz レーダー
* MacBook（処理・表示）
* iPhone / ウェアラブル（通知の受信）

### 独自技術
#### ハッカソンで開発した独自機能・技術
* レーダー処理と通信プロトコル：[firmware/proj_cm55/ibuki.c](firmware/proj_cm55/ibuki.c)
  （チャープごとの DC 除去・ハン窓・64 点 FFT、距離 15〜101 cm の複素データを送信。commit 917dbb7、db8e8f2）
* レーダー設定をプリセット P1（58.5〜62.5 GHz、距離分解能 3.75 cm）に変更：commit 55fd0f6
* ファームウェアの自動復帰：FIFO オーバーフローでレーダーを再起動、1 秒フレームが来なければウォッチドッグが再起動（commit 8d30423、827023d、実機テスト [firmware/tools/recovery_test.py](firmware/tools/recovery_test.py)）
* サーバー：ボード → Brain → WebSocket、警告時の LED と通知、USB 抜き差しからの自動再接続：[pc/ibuki/server.py](pc/ibuki/server.py)（commit 50cdc95）
* ボードの揺れの検出：普段は静かな反射（壁・家具）がすべて同時に動いたら揺れと判定し、呼吸を「不明」にする：[pc/ibuki/shake.py](pc/ibuki/shake.py)（commit 177a365）
* ライブ表示ツール：距離ごとの動き・動きの量・呼吸の波形を表示（commit 2b83df6）
* Web ダッシュボード：[web/](web/)（commit fd42b05）

## English summary
IBUKI is a camera-free, contact-free breathing monitor for babies. A 60 GHz radar on the crib measures tiny chest movements. A laptop server computes the breathing wave and movement, shows them on a web dashboard, and notifies the parents' phone or wearable. Everything runs locally. Not a medical device.

Done and tested on the board: radar firmware (~16.6 lines/s, no lost data, recovers by itself from FIFO overflow and stalls), server (WebSocket, LED, ntfy push, reconnects after an unplug), board-shake detection, live radar viewer (detects a person at ~50 cm). In progress: the breathing-rate/state Brain, connecting the dashboard, confirming the push on the phone.

## チーム
* Manami Ando
* RamadhanAdam
* （他のメンバーを追加してください）
