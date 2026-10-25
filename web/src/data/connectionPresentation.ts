import type { ServerHealth } from './health';
import type { ConnectionState } from './webSocketSource';

export interface ConnectionInput {
  connection: ConnectionState;
  board: boolean | null;
  ready: boolean;
  health: ServerHealth | null;
  placeholder: boolean;
  shaking: boolean;
  waveAvailable: boolean;
}
type Severity = 'neutral' | 'positive' | 'caution';
type StatusKey = 'connecting' | 'disconnected' | 'sensor-disconnected' | 'stale' |
  'unknown-input' | 'waiting' | 'replay' | 'placeholder' | 'unverified-source' | 'wave-unavailable' | 'receiving';

export function connectionPresentation(input: ConnectionInput) {
  const { connection, board, ready, health, shaking, waveAvailable } = input;
  const replay = health?.source === 'FileReplay';
  const placeholder = input.placeholder || health?.brain === 'placeholder';
  // Provenance stays visible even when transport/input problems take priority.
  const provenance = [
    ...(replay ? ['録画データ · 実測ライブではありません'] : []),
    ...(placeholder ? ['暫定処理 · placeholder'] : []),
  ];
  const result = (key: StatusKey, title: string, description: string, severity: Severity = 'neutral') =>
    ({ key, title, description, severity, provenance });

  // Socket boundaries outrank previously received board/health metadata.
  if (connection === 'disconnected') return result('disconnected', 'サーバーに接続できません', '接続を再試行しています', 'caution');
  if (connection === 'connecting') return result('connecting', 'サーバーに接続しています', 'データの受信を待っています');
  // Only the existing WebSocket board event is authoritative for input state.
  if (board === false) return result('sensor-disconnected', replay ? '録画データの入力が停止しています' : 'センサーが接続されていません', '現在の測定値は表示できません', 'caution');
  if (connection === 'stale') return result('stale', 'データの更新が停止しています', '新しいデータを受信できていません', 'caution');
  if (board === null) return result('unknown-input', replay ? '入力の接続状態を確認中です' : 'センサーの接続状態を確認中です', '入力元の確認を待っています');
  if (!ready) return result('waiting', '新しいデータを待っています', '測定情報の更新を確認しています');
  if (replay) return result('replay', '録画データを再生しています', placeholder ? '暫定処理の録画データです。呼吸数は未判定です' : '録画された解析データを表示しています');
  if (placeholder) return result('placeholder', '暫定データを表示しています', '呼吸の有無・呼吸数は未判定です');
  if (health?.source !== 'BoardLink') return result('unverified-source', '入力元を確認しています', 'データは受信していますが、入力元は未確認です');
  if (shaking) return result('wave-unavailable', '測定データを確認できません', 'センサーの揺れが収まるのを待っています', 'caution');
  if (!waveAvailable) return result('wave-unavailable', '波形データを待っています', '解析データを受信しています。波形はまだ利用できません');
  return result('receiving', 'データを受信しています', '最新の解析データを表示しています', 'positive');
}

export function connectionDiagnostics(input: ConnectionInput) {
  const { connection, board, ready, health, placeholder, shaking, waveAvailable } = input;
  const withinConnection = connection === 'connected' || connection === 'stale';
  const boardValue = board === null ? '未確認' : board ? '接続' : '切断';
  return {
    transport: { connecting: '接続中', connected: '接続済み', disconnected: '切断・再接続待ち', stale: '接続あり・データ更新停止' }[connection],
    board: withinConnection ? boardValue : board === null ? '未確認' : `現在は未確認（前回の受信：${boardValue}）`,
    freshness: connection === 'stale' ? '3秒以上、有効なステータスの受信なし' : connection !== 'connected' ? '現在のデータは利用できません'
      : board === false ? '入力切断・現在値は表示しません' : !ready ? '更新待ち（キャッシュだけでは表示しません）' : '時刻の進行を確認済み',
    waveform: !withinConnection || connection === 'stale' || board === false || !ready ? '利用不可' : shaking ? '揺れを検知・利用不可' : waveAvailable ? 'サンプルあり' : 'サンプルなし',
    source: health?.source ?? '未確認',
    processing: placeholder ? 'placeholder（暫定処理）' : health?.brain ?? '未確認',
    health: health ? withinConnection ? '取得済み（接続・精度の保証ではありません）' : '前回取得した情報（現在は未確認）' : '未取得または取得に失敗',
  };
}

// Diagnostic URLs never display credentials, query tokens or fragments.
export function diagnosticWebSocketUrl(value: string): string {
  try {
    const url = new URL(value);
    url.username = ''; url.password = ''; url.search = ''; url.hash = '';
    return url.href;
  } catch { return '未確認'; }
}
