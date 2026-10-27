# セキュリティ（脅威モデル）

息吹～IBUKI～が扱うのは、眠っている赤ちゃんの呼吸です。カメラもマイクの録音もありませんが、呼吸のデータは家族のプライベートな情報で、アラームは命に関わる機能です。このページでは、何を守るのか、誰が何をしうるのか、今のプロトタイプがどこまで対策できていて、何がまだなのかを正直に書きます。

## 守るもの

1. **アラーム**：鳴るべきときに必ず鳴り、他人に止められないこと。いちばん大切です。
2. **赤ちゃんのデータ**：呼吸の波、状態、通知の内容を、家族以外に見せないこと。
3. **ボード**：本物のファームウェアだけが動くこと。

## 誰が何をしうるか

| 誰 | できてしまうこと |
|---|---|
| 同じ Wi-Fi にいる人（来客、パスワードを知っている近所の人） | ダッシュボードで呼吸を見る、アラームを止める |
| インターネット上の人 | 通知のトピック名を知れば、通知を読む・偽の通知を送る |
| ボードに触れる人 | 別のファームウェアを書き込む |
| 私たち自身のミス | 秘密（トピック名）を公開してしまう、など |

## 今のプロトタイプ

| 部分 | 今 | 対策 |
|---|---|---|
| ダッシュボードとライブの波形（Wi-Fi） | 暗号化なし、ログインなし | ペアリング（合言葉）を付ける。ボード単体で動かすときは暗号化（TLS） |
| アラームを止めるボタン（`POST /api/button`） | 同じ Wi-Fi の誰でも押せる | ボードのボタンか、ペアリングした端末だけ |
| スマートフォンへの通知（ntfy.sh） | 通信は HTTPS で暗号化。アクセストークン（`IBUKI_NTFY_TOKEN`）に対応済みで、予約したトピックなら名前を知られても読まれません。トークンなしではトピック名が唯一の秘密です。ntfy.sh のサーバーは通知の文を読めます | 長いランダムなトピック名とトークン、または自分たちの ntfy サーバー |
| 秘密の管理 | トピック名は `.env` に置き、git に入れない（`.gitignore`） | このまま |
| ボードとパソコン（USB） | 暗号化なし。ただしケーブルなので外からは聞けない | ボード単体で動かせば、外に出るのは通知の「出来事」だけ |
| 保存されるデータ | 開発用の録画がパソコンにファイルとして残る | 製品では生のデータを保存しない。設定は暗号化して、鍵はチップの安全な領域に |
| ファームウェア | 署名なし。誰でも書き込める | セキュアブート、署名付きの更新（PSOC Edge のセキュリティ機能） |

## 設計の考え方

- **データは家から出さない**：呼吸の処理はボードの上で行い、外に出すのは「呼吸が止まった」のような出来事だけにします。
- **ネットワークがなくても鳴る**：Wi-Fi が切れても、ボードのサイレンと LED は鳴ります。見守りが止まったときは「オフライン」と知らせます。
- **映さない、録らない**：カメラは使わず、マイクを使う場合も音はボードの外に出しません。

医療機器ではありません。

---

# Security (threat model)

息吹～IBUKI～ handles a sleeping baby's breathing. There is no camera and no audio recording, but breathing data is private family information, and the alarm is safety-critical. This page says what we protect, who could do what, and honestly where the prototype stands today.

## What we protect

1. **The alarm**: it must ring when it should, and strangers must not be able to silence it. This matters most.
2. **The baby's data**: the breathing wave, the state and the alert texts stay within the family.
3. **The board**: only genuine firmware runs on it.

## Who could do what

| Who | What they could do |
|---|---|
| Someone on the same Wi-Fi (a guest, a neighbour who knows the password) | Watch the breathing on the dashboard, stop the alarm |
| Someone on the internet | With the notification topic name: read alerts, send fake ones |
| Someone who can touch the board | Flash other firmware |
| Our own mistakes | Publishing a secret (the topic name), for example |

## The prototype today

| Part | Today | Fix |
|---|---|---|
| Dashboard and live wave (Wi-Fi) | No encryption, no login | Pairing (a shared code). Encryption (TLS) once the board runs on its own |
| The alarm's stop button (`POST /api/button`) | Anyone on the same Wi-Fi can press it | Only the board's own button or a paired device |
| Phone alerts (ntfy.sh) | Encrypted in transit (HTTPS). Access tokens are supported (`IBUKI_NTFY_TOKEN`): with a reserved topic, knowing the name isn't enough to read alerts. Without a token the topic name is the only secret. ntfy.sh's server can read the alert text | A long random topic plus a token, or our own ntfy server |
| Secrets | The topic name lives in `.env`, never committed (`.gitignore`) | Keep as is |
| Board to laptop (USB) | Not encrypted, but a cable can't be heard from outside | Once the board runs on its own, only alert events leave it |
| Stored data | Development recordings stay on the laptop as files | The product stores no raw data. Settings encrypted, keys in the chip's secure area |
| Firmware | Not signed; anyone can flash it | Secure boot and signed updates (PSOC Edge security features) |

## Design principles

- **Data stays home**: breathing is processed on the board; only events like "breathing stopped" leave it.
- **Rings without a network**: if the Wi-Fi drops, the board's siren and LED still work, and the parents are told the monitor is offline.
- **No pictures, no recordings**: no camera, and if the microphones are used, sound never leaves the board.

Not a medical device.
