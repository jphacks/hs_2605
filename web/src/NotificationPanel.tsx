import { notificationDetails, notificationMessages, notificationPresentation } from './data/notifications';
import type { NotificationState } from './data/notifications';

export default function NotificationPanel({ state }: { state: NotificationState }) {
  const view = notificationPresentation(state);
  return <section className="notification-panel panel" aria-label="警告と通知">
    <h2>現在の警告</h2>
    <p className="muted" role="status">{view.message}</p>
    <div aria-live="polite" aria-relevant="additions text">
      {view.current.map(warning => <div key={warning.entry.event.reason}
        className={`received-event received-event--alert${warning.indication === 'unknown' ? ' warning-unknown' : ''}`}>
        <strong>{notificationMessages[warning.entry.event.reason]}</strong>
        <small>{warning.indication === 'unknown'
          ? '現在の状態は確認できません。以前の警告イベントに基づく表示です。'
          : '警告イベントを受信しています。医療的な確定判断ではありません。'}</small>
      </div>)}
    </div>
    <h2 className="history-heading">最近の通知 <span>直近20件まで</span></h2>
    <p className="muted">過去の通知は受信した記録です。現在の状態や、スマートフォンへの通知の到達を示すものではありません。</p>
    {state.history.length === 0 ? <p className="muted">通知はまだありません。</p>
      : <ol className="notification-history">
        {state.history.map(entry => <li key={entry.id} className={`received-event received-event--${entry.event.type}`}>
          <strong>{entry.event.type === 'alert' ? '警告' : '情報'} · {notificationMessages[entry.event.reason]}</strong>
          <time dateTime={new Date(entry.receivedAt).toISOString()}>受信時刻：{new Date(entry.receivedAt).toLocaleTimeString('ja-JP')}</time>
          {notificationDetails(entry.event).map(detail => <small key={detail}>{detail}</small>)}
        </li>)}
      </ol>}
  </section>;
}
