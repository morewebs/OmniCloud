import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Link from '@mui/material/Link';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { api, fmtMoney, fmtRelative, post, safeHref } from '../api';
import type { BillingAccountRow, Invoice } from '../types';
import { Value } from './Value';

const SOON_DAYS = 14;

function isOpen(i: Invoice) {
  return i.open_amount != null && Number(i.open_amount.amount) > 0;
}

function day(s: string | null) {
  return s ? s.slice(0, 10) : '—';
}

/** One card per provider account: billing model, balance, what is owed and
 *  when, unpaid orders with their pay links, renewals coming up. Every
 *  missing value reads "not exposed" (the provider's API lacks it), never 0. */
export function AccountBilling({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const rows = useQuery<BillingAccountRow[]>({ queryKey: ['billing-accounts'],
    queryFn: () => api<BillingAccountRow[]>('/api/billing/accounts') });
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (rows.isPending || !rows.data?.length) return null;

  const refresh = async (id: number) => {
    setBusy(id); setError(null);
    try {
      await post(`/api/billing/accounts/${id}/refresh`, {}, 120_000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      qc.invalidateQueries({ queryKey: ['billing-accounts'] });
      qc.invalidateQueries({ queryKey: ['overview'] });
    }
  };
  const soon = Date.now() + SOON_DAYS * 86_400_000;

  return (
    <Stack spacing={1.5}>
      <Typography variant="subtitle1">Accounts</Typography>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      <Box sx={{ display: 'grid', gap: 2,
                 gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)', xl: 'repeat(3, 1fr)' } }}>
        {rows.data.map(r => {
          const b = r.billing;
          const ne = (f: string) => b?.not_exposed.includes(f) ?? false;
          const open = (b?.invoices ?? []).filter(isOpen);
          const renewals = (b?.renewals ?? [])
            .filter(x => x.date && Date.parse(x.date) <= soon)
            .sort((x, y) => Date.parse(x.date!) - Date.parse(y.date!));
          return (
            <Card key={r.account_id} sx={{ minWidth: 0 }}>
              <CardContent sx={{ py: 2, px: 2.5 }}>
                <Stack spacing={1.25}>
                  <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>{r.name}</Typography>
                    <Chip size="small" variant="outlined" label={r.adapter} />
                    <Box sx={{ flex: 1 }} />
                    {isAdmin && r.supported && (
                      <Button size="small" disabled={busy === r.account_id}
                              onClick={() => void refresh(r.account_id)}>
                        {busy === r.account_id ? 'Refreshing…' : 'Refresh'}
                      </Button>
                    )}
                  </Stack>
                  {!r.supported && (
                    <Typography variant="body2" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
                      billing not available for this provider
                    </Typography>
                  )}
                  {r.supported && !b && !r.last_error && (
                    <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                      waiting for the first billing sync
                    </Typography>
                  )}
                  {r.last_error && (
                    <Alert severity="warning" icon={false} sx={{ py: 0 }}>
                      Billing read failed: {r.last_error}
                    </Alert>
                  )}
                  {b && (
                    <>
                      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                        {b.model}
                      </Typography>
                      <Stack direction="row" spacing={3}>
                        <Box>
                          <Typography variant="caption" sx={{ color: 'text.secondary' }}>Balance</Typography>
                          <Value value={b.balance ? fmtMoney(b.balance) : null}
                                 notExposed={ne('balance')} />
                        </Box>
                        {b.upcoming && (
                          <Box>
                            <Typography variant="caption" sx={{ color: 'text.secondary' }}>Next invoice (est.)</Typography>
                            <Value value={fmtMoney(b.upcoming)} />
                          </Box>
                        )}
                        <Box>
                          <Typography variant="caption" sx={{ color: 'text.secondary' }}>Open invoices</Typography>
                          <Value value={ne('invoices') ? null : String(open.length)}
                                 notExposed={ne('invoices')} />
                        </Box>
                      </Stack>
                      {open.slice(0, 4).map(i => {
                        const overdue = !!i.due_date && Date.parse(i.due_date) < Date.now();
                        return (
                          <Typography key={i.id} variant="body2">
                            <span className="num">{fmtMoney(i.open_amount)}</span> open on {i.id}
                            {i.due_date && <> · due <span className="num">{day(i.due_date)}</span></>}
                            {' '}<Chip size="small" variant="outlined" label={i.status}
                                       color={overdue ? 'error' : 'warning'} sx={{ height: 18 }} />
                            {safeHref(i.url) && <> · <Link href={safeHref(i.url)} target="_blank" rel="noreferrer">view</Link></>}
                          </Typography>
                        );
                      })}
                      {b.unpaid_orders.map(o => (
                        <Typography key={o.id} variant="body2">
                          Order {o.id} awaits payment
                          {o.total && <> · <span className="num">{fmtMoney(o.total)}</span></>}
                          {o.due_date && <> · expires <span className="num">{day(o.due_date)}</span></>}
                          {safeHref(o.url) && <> · <Link href={safeHref(o.url)} target="_blank" rel="noreferrer">pay</Link></>}
                        </Typography>
                      ))}
                      {renewals.slice(0, 4).map(x => (
                        <Typography key={x.provider_id} variant="body2"
                                    sx={{ color: x.auto ? 'text.secondary' : 'warning.main' }}>
                          {x.name} {x.auto ? 'renews' : 'expires'} <span className="num">{day(x.date)}</span>
                          {x.auto === false && ' - no auto-renew'}
                        </Typography>
                      ))}
                      {renewals.length === 0 && !ne('renewals') && (b.renewals.length > 0) && (
                        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                          nothing renews in the next {SOON_DAYS} days
                        </Typography>
                      )}
                    </>
                  )}
                  {r.fetched_at && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      as of {fmtRelative(r.fetched_at)}
                    </Typography>
                  )}
                </Stack>
              </CardContent>
            </Card>
          );
        })}
      </Box>
    </Stack>
  );
}
