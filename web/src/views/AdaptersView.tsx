import { useQuery } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { api } from '../api';
import type { AccountRow, AdapterInfo } from '../types';
import { CAPABILITY_LABELS } from '../types';

/** Adapter registry + per-account health. Read-only in v1. */
export function AdaptersView() {
  const adapters = useQuery<AdapterInfo[]>({ queryKey: ['adapters'],
    queryFn: () => api<AdapterInfo[]>('/api/adapters') });
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });

  if (adapters.isPending || accounts.isPending) return null;
  if (adapters.isError) return <Alert severity="error">{(adapters.error as Error).message}</Alert>;

  return (
    <Stack spacing={3}>
      <Typography variant="h5">Adapters</Typography>

      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Adapter</TableCell>
            <TableCell>Capabilities</TableCell>
            <TableCell>Accounts</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {adapters.data!.map(a => {
            const users = accounts.data!.filter(x => x.adapter === a.key);
            return (
              <TableRow key={a.key}>
                <TableCell>{a.display_name}</TableCell>
                <TableCell>
                  <Stack direction="row" sx={{ flexWrap: "wrap", gap: 0.5 }}>
                    {a.capabilities.map(c => (
                      <Chip key={c} size="small" variant="outlined"
                            label={CAPABILITY_LABELS[c] ?? c} />
                    ))}
                  </Stack>
                </TableCell>
                <TableCell>
                  {users.length === 0 ? '—' : users.map(u => u.name).join(', ')}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        Roadmap adapters: OVH, Gcore, Netlen, Lightnode, Tube-hosting. Each
        maps its API onto the same canonical model; adding one adds no new UI.
      </Typography>
    </Stack>
  );
}
