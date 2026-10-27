type IconName = 'brand' | 'moon' | 'movement' | 'breathing' | 'bed' | 'bell' | 'alert' | 'empty' | 'wave' | 'menu' | 'wifi' | 'info';

// Decorative, code-native marks. Existing Japanese text supplies every meaning.
const paths: Record<IconName, string[]> = {
  brand: ['M3 11C7 4 11 4 16 11S25 18 29 11', 'M3 21C7 14 11 14 16 21S25 28 29 21'],
  moon: ['M24 21A12 12 0 0 1 12 5a12 12 0 1 0 12 16Z'],
  movement: ['M2 17h5l3-11 5 22 5-18 3 7h7'],
  breathing: ['M14 5v10l-5-5c-3 0-6 10-6 15 0 3 5 4 9 1l2-2', 'M18 5v10l5-5c3 0 6 10 6 15 0 3-5 4-9 1l-2-2', 'M16 3v10'],
  bed: ['M3 10v18M29 14v14M3 23h26M3 18h26v-5H12v5', 'M6 13h4v5H6Z'],
  bell: ['M8 13c0-6 3-9 8-9s8 3 8 9v7l3 4H5l3-4Z', 'M13 28h6'],
  alert: ['M16 6v12', 'M16 24v1'],
  empty: ['M7 7h18v18H7Z', 'M12 16h8'],
  wave: ['M2 18c4-10 9-10 14 0s10 10 14 0'],
  menu: ['M5 8h22M5 16h22M5 24h22'],
  wifi: ['M3 12a20 20 0 0 1 26 0', 'M8 18a12 12 0 0 1 16 0', 'M13 23a5 5 0 0 1 6 0', 'M16 28h.01'],
  info: ['M16 28a12 12 0 1 0 0-24 12 12 0 0 0 0 24Z', 'M16 15v8', 'M16 9v1'],
};

export default function DashboardIcon({ name }: { name: IconName }) {
  return <svg className={`dashboard-icon icon-${name}`} viewBox="0 0 32 32" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {paths[name].map((d, index) => <path key={index} d={d} />)}
  </svg>;
}
