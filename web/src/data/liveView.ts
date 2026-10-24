import type { BrainStatus } from './status';
import type { ServerHealth } from './health';
import type { ConnectionState } from './webSocketSource';

export function deriveLiveView(status: BrainStatus | null, connection: ConnectionState, board: boolean | null, ready: boolean, health: ServerHealth | null) {
  const current = connection === 'connected' && board !== false && ready ? status : null;
  const placeholder = status?.source === 'placeholder' || health?.brain === 'placeholder';
  const replay = health?.source === 'FileReplay';
  const waveAvailable = !!current && !current.shaking && current.disp_mm.length > 0;
  const notice = board === false ? '入力接続なし · 現在値は表示しません'
    : !current ? '更新されたステータスを待っています'
    : current.shaking ? 'センサーの揺れを検知 · 波形は利用できません（呼吸停止の判定ではありません）'
    : !waveAvailable ? '波形なし · 学習中または測定できない状態です'
    : placeholder ? '暫定処理の波形 · 呼吸の有無・呼吸数は未判定です' : 'Brain の受信結果 · 医療的な判定ではありません';
  return { status: current, placeholder, replay, waveAvailable, notice,
    bpm: current && waveAvailable && !placeholder ? current.bpm : null,
    sourceLabel: replay ? '録画再生（実測ライブではありません）' : health?.source === 'BoardLink' ? 'ボード入力（精度未検証）' : '入力元未確認',
  };
}
