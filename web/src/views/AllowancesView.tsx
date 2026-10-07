import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Button from '@mui/material/Button';
import Skeleton from '@mui/material/Skeleton';
import { PageHeader } from '../components/PageHeader';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { api, downloadFile, fmtCurrency, fmtMoney, fmtTime, sumOverageByCurrency, toCsv } from '../api';
import { usePageTitle } from '../usePageTitle';
import type { Allowance } from '../types';
import { AllowanceMeter } from '../components/AllowanceMeter';
import { Value } from '../components/Value';
import { AccountBilling } from '../components/AccountBilling';

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
  const billing = useQuery<{ adapter: string; currency: string; monthly_base: number;
    projected_overage: number; servers: number; price_not_exposed: boolean }[]>({
    queryKey: ['billing'], queryFn: () => api('/api/billing/summary') });
  const allowances = useQuery<AllowanceRow[]>({ queryKey: ['allowances'],
    queryFn: () => api<AllowanceRow[]>('/api/allowances') });
  const me = useQuery({ queryKey: ['me'],
    queryFn: () => api<{ role: string }>('/api/auth/me') });
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);

  if (allowances.isPending || billing.isPending) {
    return <Stack spacing={1.5}>
      <Skeleton variant="rounded" height={32} sx={{ maxWidth: 180 }} />
      {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} variant="rounded" height={44} />)}
    </Stack>;
  }
  if (allowances.isError || billing.isError) return (
    <Stack spacing={2}>
      <PageHeader title="Billing" />
      {allowances.isError && (
        <Alert severity="error"
               action={<Button onClick={() => allowances.refetch()}>Retry</Button>}>
          {(allowances.error as Error).message}
        </Alert>
      )}
      {billing.isError && (
        <Alert severity="error"
               action={<Button onClick={() => billing.refetch()}>Retry</Button>}>
          Billing summary unavailable: {(billing.error as Error).message}
        </Alert>
      )}
    </Stack>
  );

  const rows = allowances.data ?? [];
  const safePage = Math.min(page, Math.max(0, Math.ceil(rows.length / rowsPerPage) - 1));
  // overage totals grouped per currency - never summed across currencies
  const overageByCur = sumOverageByCurrency(
    (allowances.data ?? []).map(r => ({ allowance: r.allowance })));
  const fmtCur = (cur: string, amt: number) => fmtCurrency(amt, cur);

  return (
    <Stack spacing={3}>
      <PageHeader title="Billing"
        subtitle="Balances, invoices, renewals, traffic allowances and overage exposure — every number provider-reported."
        actions={
          <Button size="small" variant="outlined" disabled={rows.length === 0}
                  onClick={() =>
                    downloadFile('billing.csv', toCsv(
                      ['server', 'provider', 'allowance_bytes', 'used_bytes', 'pct',
                       'counting', 'projected_overage', 'currency', 'as_of'],
                      rows.map(r => [
                        r.name, r.adapter,
                        r.allowance.included_bytes ?? '',
                        r.allowance.used_bytes ?? '',
                        r.allowance.used_bytes != null && r.allowance.included_bytes
                          ? (r.allowance.used_bytes / r.allowance.included_bytes * 100).toFixed(1) : '',
                        r.allowance.counting ?? '',
                        r.allowance.projected_overage_cost?.amount ?? '',
                        r.allowance.projected_overage_cost?.currency ?? '',
                        r.last_seen_at,
                      ])))}>
            Download CSV
          </Button>
        } />

      <AccountBilling isAdmin={me.data?.role === 'admin'} />

      <Box>
        <Typography variant="subtitle1" gutterBottom>Billing exposure</Typography>
        {(billing.data ?? []).map(b => (
          <Stack key={`${b.adapter}-${b.currency}`} direction="row" spacing={2} sx={{ mb: 1, flexWrap: "wrap" }}>
            <Typography className="num" variant="body2">
              {b.adapter} ({b.currency}): {fmtCur(b.currency, b.monthly_base)}/mo base across {b.servers} servers
              {b.projected_overage > 0 && ` · ${fmtCur(b.currency, b.projected_overage)} projected overage`}
            </Typography>
            {b.price_not_exposed && (
              <Typography variant="caption" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
                base cost for some servers is not exposed by this provider
              </Typography>
            )}
          </Stack>
        ))}
        {overageByCur.size > 0 && (
          <Typography variant="body2" sx={{ color: 'text.secondary' }}>
            Projected overage total:{' '}
            <span className="num">
              {[...overageByCur.entries()].map(([cur, amt]) => fmtCur(cur, amt)).join(' + ')}
            </span>
          </Typography>
        )}
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
            {rows.slice(safePage * rowsPerPage, (safePage + 1) * rowsPerPage).map(r => (
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
                  <Value value={fmtMoney(r.allowance.projected_overage_cost)} />
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
        page={safePage}
        onPageChange={(_, p) => setPage(p)}
        rowsPerPage={rowsPerPage}
        onRowsPerPageChange={(e) => { setRowsPerPage(parseInt(e.target.value, 10)); setPage(0); }}
        rowsPerPageOptions={[25, 50, 100]}
      />
    </Stack>
  );
}
