# セキュリティ

息吹～IBUKI～が扱うのは、眠っている赤ちゃんの呼吸です。呼吸のデータは家族のプライベートな情報で、アラームは安全に関わる機能です。このページは、今のプロトタイプで実装している対策をまとめたものです。

## 実装している対策

| 部分 | 対策 |
|---|---|
| 見守りの方法 | 60 GHz レーダーだけを使います。カメラは使わず、音も録りません |
| アラーム | インターネットがなくても、パソコンのサイレンとボードの LED で知らせます。止められるのは、ボードのボタン（SW1）、サーバーの画面（Enter）、ダッシュボードの停止ボタンです |
| スマートフォンへの通知（ntfy） | 通信は HTTPS で暗号化。アクセストークン（`IBUKI_NTFY_TOKEN`）に対応しているので、予約したトピックなら、名前を知られても通知を読まれたり偽の通知を送られたりしません |
| 秘密の管理 | トピック名とトークンは `.env` に置き、git に入れません（`.gitignore`）。見本は [.env.example](.env.example) |
| ボードとパソコン | USB ケーブルでつなぐので、データは無線で外に出ません |
| 録画 | 開発用の録画はパソコンの中だけに置き、git に入れません。共有するのは短い見本（`recordings/samples/`）だけです |

## 使うときの注意

- ダッシュボードにはログインがありません。同じ Wi-Fi の人は開けて、アラームを止めることもできます。信頼できるネットワークでだけ使ってください。
- 医療機器ではありません。

---

# Security

息吹～IBUKI～ handles a sleeping baby's breathing. Breathing data is private family information, and the alarm is safety-critical. This page lists the protections the prototype has today.

## What is in place

| Part | Protection |
|---|---|
| How we watch | A 60 GHz radar only. No camera, and no audio is recorded |
| The alarm | Works without the internet: the laptop's siren and the board's LED. It can be stopped with the board's button (SW1), Enter in the server window, or the dashboard's stop button |
| Phone alerts (ntfy) | Encrypted in transit (HTTPS). Access tokens are supported (`IBUKI_NTFY_TOKEN`): with a reserved topic, knowing the name isn't enough to read alerts or send fake ones |
| Secrets | The topic name and token live in `.env`, never committed (`.gitignore`). Template: [.env.example](.env.example) |
| Board to laptop | A USB cable, so the data never goes over the air |
| Recordings | Development recordings stay on the laptop and out of git. Only short samples are shared (`recordings/samples/`) |

## When you use it

- The dashboard has no login: anyone on the same Wi-Fi can open it and stop the alarm. Use it on a network you trust.
- Not a medical device.
