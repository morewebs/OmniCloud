import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { api, fmtTime } from '../api';
import { usePageTitle } from '../usePageTitle';
import type { Allowance, FleetResponse } from '../types';
import { AllowanceMeter } from '../components/AllowanceMeter';
import { Value } from '../components/Value';

interface AllowanceRow {
  account_id: number;
  provider_id: string;
  name: string;
  adapter: string;
  allowance: Allowance;
  last_seen_at: string;
}

const COUNTING_TEXT: Record<string, string> = {
  outgoing_only: 'outgoing only',
  ingress_and_egress: 'in + out',
};

export function AllowancesView() {
  usePageTitle('Billing');
  const fleet = useQuery<FleetResponse>({ queryKey: ['fleet'],
    queryFn: () => api<FleetResponse>('/api/fleet') });
  const billing = useQuery<{ adapter: string; monthly_base_eur: number;
    projected_overage_eur: number; servers: number; price_not_exposed: boolean }[]>({
    queryKey: ['billing'], queryFn: () => api('/api/billing/summary') });
  const allowances = useQuery<AllowanceRow[]>({ queryKey: ['allowances'],
    queryFn: () => api<AllowanceRow[]>('/api/allowances') });
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);

  if (allowances.isPending || billing.isPending || fleet.isPending) {
    return <Typography sx={{ color: 'text.secondary' }}>Loading…</Typography>;
  }
  if (allowances.isError) return <Alert severity="error">{(allowances.error as Error).message}</Alert>;

  const rows = allowances.data ?? [];
  const totalOverage = (billing.data ?? []).reduce((a, b) => a + b.projected_overage_eur, 0);

  return (
    <Stack spacing={3}>
      <Typography variant="h5">Allowances & billing</Typography>

      <Box>
        <Typography variant="subtitle1" gutterBottom>Billing exposure</Typography>
        {(billing.data ?? []).map(b => (
          <Stack key={b.adapter} direction="row" spacing={2} sx={{ mb: 1, flexWrap: "wrap" }}>
            <Typography className="num" variant="body2">
              {b.adapter}: €{b.monthly_base_eur.toFixed(2)}/mo base across {b.servers} servers
              {b.projected_overage_eur > 0 && ` · €${b.projected_overage_eur.toFixed(2)} projected overage`}
            </Typography>
            {b.price_not_exposed && (
              <Typography variant="caption" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
                base cost for some servers is not exposed by this provider
              </Typography>
            )}
          </Stack>
        ))}
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Projected overage total: <span className="num">€{totalOverage.toFixed(2)}</span>
        </Typography>
      </Box>

      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 700 }}>
          <TableHead>
            <TableRow>
              <TableCell>Server</TableCell>
              <TableCell>Provider</TableCell>
              <TableCell>Allowance</TableCell>
              <TableCell>Rule</TableCell>
              <TableCell align="right">Projected overage</TableCell>
              <TableCell align="right">As of</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.slice(page * rowsPerPage, (page + 1) * rowsPerPage).map(r => (
              <TableRow key={`${r.account_id}:${r.provider_id}`}>
                <TableCell>{r.name}</TableCell>
                <TableCell>{r.adapter}</TableCell>
                <TableCell><AllowanceMeter allowance={r.allowance} /></TableCell>
                <TableCell>
                  {r.allowance.counting ? (
                    <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', display: 'inline-flex' }}>
                      <span>{COUNTING_TEXT[r.allowance.counting] ?? r.allowance.counting}</span>
                      {r.allowance.window && (
                        <Tooltip title={r.allowance.window}>
                          <Typography className="num" variant="caption"
                                      aria-label={`How traffic is counted: ${r.allowance.window}`}
                                      sx={{
                                        color: 'text.secondary', cursor: 'help',
                                        border: 1, borderColor: 'divider', borderRadius: '50%',
                                        width: 14, height: 14, lineHeight: '14px',
                                        textAlign: 'center', display: 'inline-block',
                                      }}>?</Typography>
                        </Tooltip>
                      )}
                    </Stack>
                  ) : '—'}
                </TableCell>
                <TableCell align="right">
                  <Value
                    value={r.allowance.projected_overage_cost
                      ? `€${Number(r.allowance.projected_overage_cost.amount).toFixed(2)}` : null}
                  />
                </TableCell>
                <TableCell align="right"><span className="num">{fmtTime(r.last_seen_at)}</span></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      <TablePagination
        component="div"
        count={rows.length}
        page={page}
        onPageChange={(_, p) => setPage(p)}
        rowsPerPage={rowsPerPage}
        onRowsPerPageChange={(e) => { setRowsPerPage(parseInt(e.target.value, 10)); setPage(0); }}
        rowsPerPageOptions={[25, 50, 100]}
      />
    </Stack>
  );
}
