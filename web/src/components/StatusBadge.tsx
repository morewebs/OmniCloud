import Chip from '@mui/material/Chip';

// Status = colored dot + mandatory text (never a bare dot).
// Colors are the desaturated trio from the design contracts.
const MAP: Record<string, { label: string; color: 'success' | 'error' | 'default' | 'warning' }> = {
  running: { label: 'Running', color: 'success' },
  off: { label: 'Off', color: 'default' },
  rebuilding: { label: 'Rebuilding', color: 'warning' },
  unknown: { label: 'Unknown', color: 'default' },
};

export function StatusBadge({ status }: { status: string }) {
  const s = MAP[status] ?? MAP.unknown;
  return (
    <Chip
      size="small"
      label={s.label}
      color={s.color === 'default' ? undefined : s.color}
      variant="outlined"
      sx={{ height: 20 }}
    />
  );
}
