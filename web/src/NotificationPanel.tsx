import { notificationDetails, notificationLevels, notificationMessages, notificationPresentation } from './data/notifications';
import type { NotificationState } from './data/notifications';
import DashboardIcon from './DashboardIcon';

const levelClass = { 緊急: 'urgent', 注意: 'caution', お知らせ: 'info' } as const;

export default function NotificationPanel({ state }: { state: NotificationState }) {
  const view = notificationPresentation(state);
  return <section className="notification-panel panel" aria-label="通知" id="notifications">
    <div className="notification-heading"><h2 className="section-heading"><DashboardIcon name="bell" />通知履歴</h2><span>この画面で受信 · 最新20件</span></div>
    <div aria-live="polite" aria-relevant="additions text">
      {view.current.map(warning => <p key={warning.entry.event.reason}
        className={`current-warning current-warning--${levelClass[notificationLevels[warning.entry.event.reason]]}${warning.indication === 'unknown' ? ' current-warning--unknown' : ''}`}>
        <strong>続いています：{notificationMessages[warning.entry.event.reason]}</strong>
        {warning.indication === 'unknown' && <span>（いまの状態は確認できません）</span>}
      </p>)}
    </div>
    {state.history.length === 0 ? <p className="muted">通知はまだありません。</p>
      : <ol className="notification-history">
        {state.history.map(entry => {
          const level = notificationLevels[entry.event.reason];
          return <li key={entry.id} className={`notice notice--${levelClass[level]}`}>
            <time dateTime={new Date(entry.receivedAt).toISOString()} title={new Date(entry.receivedAt).toLocaleTimeString('ja-JP')}>{new Date(entry.receivedAt).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })}</time>
            <span className="notice-dot" aria-hidden="true" />
            <div className="notice-body"><span className="notice-level">【{level}】</span><strong>{notificationMessages[entry.event.reason]}</strong></div>
            <div className="notice-details">{notificationDetails(entry.event).map(detail => <small key={detail}>{detail}</small>)}</div>
          </li>;
        })}
      </ol>}
  </section>;
}
