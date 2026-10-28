import type { ServerHealth } from './data/health';
import { boardItems } from './data/boardView';
import DashboardIcon from './DashboardIcon';

// What decides and what the board reports about itself (its AI chip, Wi-Fi, sensors).
export default function BoardPanel({ health }: { health: ServerHealth | null }) {
  const items = boardItems(health);
  if (items.length === 0) return null;
  return <section className="panel board-panel" aria-label="ボードの様子">
    <div className="panel-heading"><h2><DashboardIcon name="info" /> ボードの様子</h2><span className="chart-range">お知らせのみ</span></div>
    <dl className="board-items">{items.map(item => <div key={item.key} className={`board-item board-item--${item.tone}`} data-key={item.key}>
      <dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>
    <p className="muted board-note">ボードは AI 用チップで自分でも判断し、Wi-Fi につながっていればパソコンなしでスマートフォンに通知します。警告のときはボードのランプが点滅し、SW1 で止められます。</p>
  </section>;
}
