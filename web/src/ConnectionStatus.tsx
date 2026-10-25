import { connectionDiagnostics, connectionPresentation, diagnosticWebSocketUrl } from './data/connectionPresentation';
import type { ConnectionInput } from './data/connectionPresentation';

export default function ConnectionStatus(props: ConnectionInput & { url: string; notice: string }) {
  const view = connectionPresentation(props);
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
      <span className="connection-icon" aria-hidden="true">{view.severity === 'positive' ? '✓' : view.severity === 'caution' ? '!' : '·'}</span>
      <div className="connection-copy">
        <p aria-live="polite" aria-atomic="true"><strong>{view.title}</strong><span>{view.description}</span></p>
        {view.provenance.length > 0 && <div className="connection-provenance">{view.provenance.map(label => <span key={label}>{label}</span>)}</div>}
      </div>
    </div>
    <details className="connection-details">
      <summary>接続の詳細</summary>
      <div className="connection-diagnostics">
        <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        <p>{props.notice}</p>
        <p>サーバーへの接続とセンサーの接続は別です。データ受信は医療的な安全確認ではありません。</p>
      </div>
    </details>
  </section>;
}
