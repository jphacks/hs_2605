import type { ServerHealth } from './health';

export interface BoardItem { key: string; label: string; value: string; tone: 'neutral' | 'positive' | 'caution' }

const aiWords: Record<string, string> = { breathing: '呼吸しています', stopped: '呼吸が確認できません', moving: '動いています', empty: 'ベッドにいません' };
const RECENT_S = 10 * 60; // a restart or a move in the last 10 minutes is worth mentioning

function clock(unixS: number): string {
  return new Date(unixS * 1000).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
}

// Who decides, and what the board itself reports. Information only: never an alarm.
export function boardItems(health: ServerHealth | null, nowS: number = Date.now() / 1000): BoardItem[] {
  if (!health) return [];
  const items: BoardItem[] = [];
  const brain = health.brain === 'state_model' ? 'AI モデル' : health.brain === 'brain' ? 'ルール' : health.brain === 'placeholder' ? '暫定の処理' : health.brain;
  items.push({ key: 'brain', label: '判定', value: brain, tone: 'neutral' });
  const b = health.board_health;
  if (!b) return items;
  if (b.board_ai) {
    items.push({ key: 'board-ai', label: 'ボードの AI', value: aiWords[b.board_ai.state] ?? b.board_ai.state,
      tone: b.board_ai.state === 'stopped' ? 'caution' : 'neutral' });
  }
  if (b.wifi) {
    items.push({ key: 'wifi', label: 'ボードの Wi-Fi',
      value: b.wifi.connected ? `接続中${b.wifi.alerts_sent > 0 ? `・通知 ${b.wifi.alerts_sent} 回送信` : ''}` : '未接続（パソコンから通知します）',
      tone: b.wifi.connected ? 'positive' : 'neutral' });
  }
  if (b.climate) {
    items.push({ key: 'climate', label: 'ボードの温度・湿度',
      value: `${b.climate.board_temp_c.toFixed(1)}℃・${Math.round(b.climate.humidity_pct)}%`, tone: 'neutral' });
  }
  const restart = b.last_restart && nowS - b.last_restart.unix_s < RECENT_S ? b.last_restart : null;
  const move = b.last_move && nowS - b.last_move.unix_s < RECENT_S ? b.last_move : null;
  if (b.restarts !== undefined || b.moves !== undefined) {
    const value = restart ? `レーダーが再起動しました（${clock(restart.unix_s)}、${restart.reason}）`
      : move ? `ボードが動かされました（${clock(move.unix_s)}）。レーダーの向きを確認してください`
      : '正常';
    items.push({ key: 'board', label: 'ボード', value, tone: restart || move ? 'caution' : 'positive' });
  }
  if (health.pairing) items.push({ key: 'pairing', label: 'アラームの停止', value: 'ペアリングした端末だけ', tone: 'neutral' });
  return items;
}
