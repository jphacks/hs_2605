import { connectionDiagnostics, connectionPresentation, diagnosticWebSocketUrl } from './data/connectionPresentation';
import type { ConnectionInput } from './data/connectionPresentation';
import type { RefObject } from 'react';
import DashboardIcon from './DashboardIcon';

export default function ConnectionStatus(props: ConnectionInput & { url: string; notice: string; detailsRef?: RefObject<HTMLDetailsElement | null>; receivedAt?: number | null }) {
  const view = connectionPresentation(props);
  const compactTitle = view.key === 'replay' ? '録画再生中' : view.key === 'receiving' ? '解析データ受信中' : view.title;
  const diagnostics = connectionDiagnostics(props);
  const rows = [
    ['サーバー接続（WebSocket）', diagnostics.transport],
    ['接続先 URL', diagnosticWebSocketUrl(props.url)],
    ['入力接続（サーバーの board イベント）', diagnostics.board],
    ['データの更新', diagnostics.freshness],
    ['波形の利用可否', diagnostics.waveform],
    ['入力元', diagnostics.source],
    ['Brain 処理モード', diagnostics.processing],
    ['ヘルス情報', diagnostics.health],
  ];
  return <section className={`connection-status connection-status--${view.severity}`} aria-label="接続状況" data-status={view.key}>
    <div className="connection-strip">
      <span className="connection-icon" aria-hidden="true"><DashboardIcon name={view.severity === 'positive' ? 'wifi' : view.severity === 'caution' ? 'alert' : 'wave'} /></span>
      <div className="connection-copy">
        <p aria-live="polite" aria-atomic="true"><strong>{compactTitle}</strong></p>
      </div>
    </div>
    {view.provenance.length > 0 && <div className="connection-provenance">{view.provenance.map(label => <span key={label}>{label}</span>)}</div>}
    <div className="connection-meta"><span className="last-received">{props.receivedAt ? <>最終受信 <time dateTime={new Date(props.receivedAt).toISOString()}>{new Date(props.receivedAt).toLocaleTimeString('ja-JP')}</time></> : '受信待ち'}</span></div>
    <details className="connection-details" ref={props.detailsRef} id="connection-details">
      <summary title="接続の詳細"><span className="connection-details-label">接続の詳細</span><DashboardIcon name="info" /></summary>
      <div className="connection-diagnostics">
        <p className="diagnostic-summary">{view.title} — {view.description}</p>
        <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        <p>{props.notice}</p>
        <p>サーバーへの接続とセンサーの接続は別です。データ受信は医療的な安全確認ではありません。</p>
      </div>
    </details>
  </section>;
}
