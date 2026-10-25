import type { BrainStatus } from './status';
import type { AlertEvent, InfoEvent, ConnectionState } from './webSocketSource';

export type NotificationEvent = AlertEvent | InfoEvent;
export interface HistoryEntry { id: number; event: NotificationEvent; receivedAt: number }
export type WarningIndication = 'indicated' | 'unknown' | 'not-indicated';
export interface CurrentWarning {
  entry: HistoryEntry;
  indication: WarningIndication;
  afterT: number | null;
}
export interface NotificationState {
  history: HistoryEntry[];
  warnings: Partial<Record<AlertEvent['reason'], CurrentWarning>>;
  nextId: number;
  connection: ConnectionState;
  board: boolean | null;
  lastStatusT: number | null;
  ready: boolean;
}
export type NotificationAction =
  | { type: 'event'; event: NotificationEvent; receivedAt: number }
  | { type: 'status'; status: BrainStatus }
  | { type: 'connection'; value: ConnectionState }
  | { type: 'board'; connected: boolean };

// The phone notification's title and urgency (pc/ibuki/server.py), so both say the same.
export const notificationMessages: Record<NotificationEvent['reason'], string> = {
  no_breath: '呼吸が確認できません',
  left_bed: 'ベッドにいません',
  woke_up: '起きました',
  fast_breathing: '呼吸が速い状態が続いています',
};
export type NotificationLevel = '緊急' | '注意' | 'お知らせ';
export const notificationLevels: Record<NotificationEvent['reason'], NotificationLevel> = {
  no_breath: '緊急', left_bed: '注意', fast_breathing: '注意', woke_up: 'お知らせ',
};

export function createNotificationState(): NotificationState {
  return { history: [], warnings: {}, nextId: 1, connection: 'connecting', board: null, lastStatusT: null, ready: false };
}

function unknownWarnings(state: NotificationState, resetClock = false): NotificationState['warnings'] {
  return Object.fromEntries(Object.entries(state.warnings).map(([reason, warning]) => [reason,
    warning.indication === 'not-indicated' ? warning : {
      ...warning, indication: 'unknown', afterT: resetClock ? null : warning.afterT,
    },
  ]));
}

// Pure presentation bookkeeping. It never changes Brain statuses, wave samples,
// transport timers, or the backend. "not-indicated" is not medical resolution.
export function reduceNotifications(state: NotificationState, action: NotificationAction): NotificationState {
  if (action.type === 'event') {
    const entry = { id: state.nextId, event: { ...action.event }, receivedAt: action.receivedAt };
    const next = { ...state, nextId: state.nextId + 1, history: [entry, ...state.history].slice(0, 20) };
    if (action.event.type === 'info') return next;
    const afterT = action.event.t === undefined ? state.lastStatusT
      : Math.max(action.event.t, state.lastStatusT ?? action.event.t);
    return { ...next, warnings: { ...state.warnings, [action.event.reason]: {
      entry, indication: state.ready ? 'indicated' : 'unknown', afterT,
    } } };
  }
  if (action.type === 'connection') {
    if (action.value === state.connection) return state;
    return { ...state, connection: action.value,
      board: action.value === 'connecting' ? null : state.board,
      ready: false, lastStatusT: null, warnings: unknownWarnings(state, true) };
  }
  if (action.type === 'board') {
    if (action.connected === state.board) return state;
    return { ...state, board: action.connected, ready: false,
      lastStatusT: null, warnings: unknownWarnings(state, true) };
  }
  const status = action.status;
  if (state.connection !== 'connected' || state.board === false) {
    return { ...state, ready: false, lastStatusT: null, warnings: unknownWarnings(state) };
  }
  const progresses = state.lastStatusT !== null && status.t > state.lastStatusT;
  const resetClock = state.lastStatusT !== null && status.t < state.lastStatusT;
  if (!progresses || status.shaking || status.source === 'placeholder') {
    return { ...state, ready: false, lastStatusT: status.t, warnings: unknownWarnings(state, resetClock) };
  }
  const warnings = Object.fromEntries(Object.entries(state.warnings).map(([reason, warning]) => {
    if (warning.indication === 'not-indicated') return [reason, warning];
    // A status preceding an event (or the same t repeated) cannot dismiss it.
    if (warning.afterT !== null && status.t <= warning.afterT) return [reason, { ...warning, indication: 'unknown' }];
    let indication: WarningIndication = 'unknown';
    if (reason === 'no_breath') {
      if (status.present && (status.state === 'ALERT' || status.state === 'CHECK')) indication = 'indicated';
      if (status.present && (status.state === 'OK' || status.state === 'STILL')) indication = 'not-indicated';
      // EMPTY alone cannot establish the prior breathing-motion condition.
    } else if (reason === 'left_bed') {
      if (!status.present && status.state === 'EMPTY') indication = 'indicated';
      if (status.present && status.state !== 'EMPTY') indication = 'not-indicated';
    }
    return [reason, { ...warning, indication }];
  }));
  return { ...state, ready: true, lastStatusT: status.t, warnings };
}

export function notificationPresentation(state: NotificationState) {
  const current = Object.values(state.warnings)
    .filter(warning => warning.indication !== 'not-indicated')
    .sort((a, b) => b.entry.id - a.entry.id);
  const unknown = !state.ready || state.connection !== 'connected' || state.board === false || current.some(warning => warning.indication === 'unknown');
  const message = unknown ? '現在の状態は確認できません'
    : current.length ? '受信イベントと、その後の解析状態に基づく表示です'
    : Object.keys(state.warnings).length ? '現在の解析状態では継続していません'
    : '警告イベントは受信していません';
  return { current, unknown, message };
}

export function notificationDetails(event: NotificationEvent): string[] {
  const details: string[] = [];
  if (event.since_s > 0 && (event.reason === 'no_breath' || event.reason === 'fast_breathing')) {
    details.push(`${event.since_s}秒続いたとき`);
  }
  if (event.bpm !== undefined) details.push(`呼吸数 ${event.bpm}回／分`);
  return details;
}
