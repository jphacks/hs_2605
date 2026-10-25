import type { BreathingState } from './status';

export type StatusTone = 'still' | 'ok' | 'check' | 'alert' | 'empty' | 'shake' | 'wait';
export interface StatusView { tone: StatusTone; title: string; detail: string }

// The words of the README's table and of the phone notifications, so all three say the same.
export const stateWords: Record<BreathingState, StatusView> = {
  STILL: { tone: 'still', title: '眠っています', detail: 'じっとしていて、呼吸が検知できています' },
  OK: { tone: 'ok', title: '起きています', detail: '体が動いています。動いている間は呼吸を正しく測れません' },
  CHECK: { tone: 'check', title: '呼吸を確認中', detail: '呼吸が検知できていません。このまま続くと警告します' },
  ALERT: { tone: 'alert', title: '呼吸が確認できません', detail: 'すぐに赤ちゃんの様子を確認してください' },
  EMPTY: { tone: 'empty', title: 'ベッドにいません', detail: '赤ちゃんがベッドにいません' },
};

export function describeStatus(input: { state: BreathingState | null; shaking: boolean; placeholder: boolean; waiting: string }): StatusView {
  if (input.state === null) return { tone: 'wait', title: 'データを待っています', detail: input.waiting };
  if (input.placeholder) return { tone: 'wait', title: '呼吸は判定していません', detail: '暫定の処理で動いています（Brain が読み込まれていません）' };
  if (input.state === 'ALERT') return stateWords.ALERT; // an alarm stays on screen even while shaking
  if (input.shaking) return { tone: 'shake', title: 'センサーが揺れています', detail: '揺れている間は呼吸を判定しません（呼吸が止まったという意味ではありません）' };
  return stateWords[input.state];
}
