// Display normalization only. This is not a room/crib coordinate transform.
export const PLOT_RADIUS=1.45;
export function planePoint(position,extent){
  return [position.x/extent*PLOT_RADIUS,position.y/extent*PLOT_RADIUS,0];
}
