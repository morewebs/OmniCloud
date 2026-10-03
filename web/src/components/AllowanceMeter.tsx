import { Box, LinearProgress, Typography } from '@mui/material';
import type { Allowance } from '../types';
import { fmtBytes } from '../api';

// Meter thresholds match the status definitions exactly: amber > 80%,
// red > 95% or overage. Every meter has its number adjacent. No meter at all
// when used_bytes is null (never a cosmetically filled track).
export function AllowanceMeter({ allowance }: { allowance: Allowance }) {
  const { included_bytes, used_bytes } = allowance;
  if (used_bytes == null) {
    return <Typography variant="body2" sx={{ color: 'text.secondary' }}>—</Typography>;
  }
  if (included_bytes == null || included_bytes === 0) {
    return (
      <Typography className="num" variant="body2">
        {fmtBytes(used_bytes)}
      </Typography>
    );
  }
  const pct = (used_bytes / included_bytes) * 100;
  const color = pct > 95 ? 'error' : pct > 80 ? 'warning' : 'primary';
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 140 }}>
      <LinearProgress
        variant="determinate"
        value={Math.min(pct, 100)}
        color={color as 'primary' | 'warning' | 'error'}
        sx={{ flex: 1 }}
      />
      <Typography className="num" variant="body2" noWrap>
        {fmtBytes(used_bytes)} / {fmtBytes(included_bytes)}
      </Typography>
    </Box>
  );
}
