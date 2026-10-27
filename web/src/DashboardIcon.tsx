type IconName = 'brand' | 'moon' | 'movement' | 'breathing' | 'bed' | 'bell' | 'alert' | 'empty' | 'wave';

// Decorative, code-native marks. Existing Japanese text supplies every meaning.
const paths: Record<IconName, string[]> = {
  brand: ['M3 17c4-10 9-12 15-6s10 6 12-3', 'M2 25c5-7 9-7 15-3s10 4 14-3', 'M7 11c2-7 8-9 13-3'],
  moon: ['M24 21A12 12 0 0 1 12 5a12 12 0 1 0 12 16Z'],
  movement: ['M2 17h5l3-11 5 22 5-18 3 7h7'],
  breathing: ['M14 5v10l-5-5c-3 0-6 10-6 15 0 3 5 4 9 1l2-2', 'M18 5v10l5-5c3 0 6 10 6 15 0 3-5 4-9 1l-2-2', 'M16 3v10'],
  bed: ['M3 10v18M29 14v14M3 23h26M3 18h26v-5H12v5', 'M6 13h4v5H6Z'],
  bell: ['M8 13c0-6 3-9 8-9s8 3 8 9v7l3 4H5l3-4Z', 'M13 28h6'],
  alert: ['M16 6v12', 'M16 24v1'],
  empty: ['M7 7h18v18H7Z', 'M12 16h8'],
  wave: ['M2 18c4-10 9-10 14 0s10 10 14 0'],
};

export default function DashboardIcon({ name }: { name: IconName }) {
  return <svg className="dashboard-icon" viewBox="0 0 32 32" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {paths[name].map((d, index) => <path key={index} d={d} />)}
  </svg>;
}
