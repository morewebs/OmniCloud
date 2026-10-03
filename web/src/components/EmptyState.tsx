import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import type { SvgIconProps } from '@mui/material/SvgIcon';
import DnsIcon from '@mui/icons-material/Dns';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import StorefrontIcon from '@mui/icons-material/Storefront';
import KeyIcon from '@mui/icons-material/Key';

/** Empty states: one icon, one line, one action. Icons are real glyphs —
 *  they render identically on every platform (no symbol-font roulette). */
const MARKS: Record<string, React.ComponentType<SvgIconProps>> = {
  server: DnsIcon,
  orders: ReceiptLongIcon,
  catalog: StorefrontIcon,
  credentials: KeyIcon,
};

export function EmptyState({ mark = 'server', line, actionLabel, onAction }: {
  mark?: 'server' | 'orders' | 'catalog' | 'credentials';
  line: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  const Icon = MARKS[mark] ?? DnsIcon;
  return (
    <Stack spacing={1.5} sx={{ py: 8, textAlign: 'center', alignItems: 'center' }}>
      <Icon sx={{ fontSize: 40, color: 'text.secondary', opacity: 0.4 }} />
      <Typography variant="body1" sx={{ color: 'text.secondary' }}>{line}</Typography>
      {actionLabel && onAction && (
        <Button variant="contained" onClick={onAction}>{actionLabel}</Button>
      )}
    </Stack>
  );
}
