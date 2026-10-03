/**
 * Chart contract for OmniCloud (design.md §2): the ONLY chromatic elements on
 * any chart are the status trio and the brand accent. x-charts' default
 * palettes (blue-first) never reach the screen. Axis numbers are mono.
 */
import type { ChartsAxisSlotProps } from '@mui/x-charts/ChartsAxis';

// design.md 2: the status trio, lightened one step for dark paper
export const STATUS_COLORS: Record<string, Record<'light' | 'dark', string>> = {
  running: { light: '#3F6212', dark: '#A3E635' },
  off: { light: '#71717A', dark: '#A1A1AA' },      // 3:1 on white in light
  unknown: { light: '#71717A', dark: '#A1A1AA' },
  rebuilding: { light: '#854D0E', dark: '#FBBF24' },
};

// spend bars: one brand-shade step per currency (never summing, per contract)
export const SPEND_COLORS: Record<'light' | 'dark', string[]> = {
  light: ['#5E6AD2', '#9D9DF5'],
  dark: ['#7C7CF0', '#9D9DF5'],
};

export function statusColor(status: string, mode: 'light' | 'dark'): string {
  return (STATUS_COLORS[status] ?? STATUS_COLORS.unknown)[mode];
}

// mono tick labels on every chart axis (bar/line; the donut has no axes)
export const axisSlotProps: ChartsAxisSlotProps = {
  axisTickLabel: {
    style: { fontFamily: '"Geist Mono", ui-monospace, monospace', fontSize: 11 },
  },
};
