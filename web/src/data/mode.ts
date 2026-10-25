export type DashboardMode = 'DEMO' | 'LIVE';

// Explicit developer opt-in only. No saved preference or connection fallback.
export function selectDashboardMode(development: boolean, search: string): DashboardMode {
  return development && new URLSearchParams(search).get('demo') === '1' ? 'DEMO' : 'LIVE';
}

export function createModeSource<T>(mode: DashboardMode, factories: { live: () => T; demo: () => T }): T {
  return mode === 'DEMO' ? factories.demo() : factories.live();
}
