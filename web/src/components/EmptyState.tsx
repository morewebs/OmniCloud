import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

/** Empty states: one composed mark, one line, one action. */
export function EmptyState({ mark = '∅', line, actionLabel, onAction }: {
  mark?: string;
  line: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <Stack spacing={1.5} sx={{ py: 8, textAlign: 'center', alignItems: 'center' }}>
      <Typography className="num" variant="h3" sx={{ color: 'text.secondary', opacity: 0.5 }}>
        {mark}
      </Typography>
      <Typography variant="body1" sx={{ color: 'text.secondary' }}>{line}</Typography>
      {actionLabel && onAction && (
        <Button variant="contained" onClick={onAction}>{actionLabel}</Button>
      )}
    </Stack>
  );
}
