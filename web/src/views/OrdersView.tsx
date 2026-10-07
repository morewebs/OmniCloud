import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Link from '@mui/material/Link';
import Dialog from '@mui/material/Dialog';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { api, fmtTime, post } from '../api';

/** estimated_monthly is JSON in a TEXT column - parse defensively: one
 * corrupted legacy row must never throw in render (white-screen). */
function est(o: OrderRow): { amount: string; currency: string; partial?: boolean } | null {
  try { return JSON.parse(o.estimated_monthly); } catch { return null; }
}
import { PageHeader } from '../components/PageHeader';
import { EmptyState } from '../components/EmptyState';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Toast } from '../components/Toast';
import { usePageTitle } from '../usePageTitle';
import type { ToastMsg } from '../components/Toast';
import type { OrderRow } from '../types';

const PAST: Record<string, string> = { confirm: 'confirmed', cancel: 'cancelled', execute: 'executed' };
const STATUS_COLOR: Record<string, 'success' | 'error' | 'warning' | 'info' | undefined> = {
  provisioned: 'success', failed: 'error', executing: 'warning', awaiting_payment: 'info',
};

export function OrdersView() {
  usePageTitle('Orders');
  const qc = useQueryClient();
  const navigate = useNavigate();
  const orders = useQuery<OrderRow[]>({ queryKey: ['orders'],
    queryFn: () => api<OrderRow[]>('/api/orders') });
  const me = useQuery({ queryKey: ['me'],
    queryFn: () => api<{ role: string }>('/api/auth/me') });
  const isAdmin = me.data?.role === 'admin';
  const [detailId, setDetailId] = useState<number | null>(null);
  const [toast, setToast] = useState<ToastMsg>(null);
  // one order action in flight at a time: a double-click must not fire
  // duplicate confirm/cancel/execute mutations
  const [actBusy, setActBusy] = useState<number | null>(null);
  // a REAL order's Execute spends money at the provider: typed confirm first
  const [executeReal, setExecuteReal] = useState<OrderRow | null>(null);

  const act = async (id: number, verb: string) => {
    if (actBusy != null) return;
    setActBusy(id);
    try {
      await post(`/api/orders/${id}/${verb}`);
      qc.invalidateQueries({ queryKey: ['orders'] });
      qc.invalidateQueries({ queryKey: ['overview'] });
      setToast({ message: `Order ${PAST[verb] ?? verb + 'ed'}`, severity: 'success' });
    } catch (e) {
      setToast({ message: (e as Error).message, severity: 'error' });
    } finally {
      setActBusy(null);
    }
  };

  if (orders.isPending) return <Typography sx={{ color: 'text.secondary' }}>Loading…</Typography>;
  if (orders.isError) return <Alert severity="error">{(orders.error as Error).message}</Alert>;

  return (
    <Stack spacing={3}>
      <PageHeader title="Orders"
                  subtitle="Draft, confirm, execute. Prototype orders create nothing; real orders (an account with purchases on) buy at the provider." />

      {orders.data!.length === 0
        ? <EmptyState mark="orders" line="No orders yet. Browse the catalog to place one."
                      actionLabel="Open catalog" onAction={() => navigate('/catalog')} />
        : (
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>#</TableCell>
                  <TableCell>Plan</TableCell>
                  <TableCell>Provider</TableCell>
                  <TableCell>Location</TableCell>
                  <TableCell align="right">Est. monthly</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>By</TableCell>
                  <TableCell>Created</TableCell>
                  {isAdmin && <TableCell align="right">Actions</TableCell>}
                </TableRow>
              </TableHead>
              <TableBody>
                {orders.data!.map(o => (
                  <TableRow key={o.id} hover tabIndex={0} aria-label={`Order ${o.id}`}
                            onClick={() => setDetailId(o.id)}
                            onKeyDown={e => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                setDetailId(o.id);
                              }
                            }}
                            sx={{ cursor: 'pointer' }}>
                    <TableCell className="num">{o.id}</TableCell>
                    <TableCell sx={{ fontWeight: 500 }}>
                      {o.kind === 'ip' ? `extra IP${o.resulting_provider_id ? ` ${o.resulting_provider_id}` : ''}`
                                       : o.plan_name}
                    </TableCell>
                    <TableCell><Chip size="small" variant="outlined" label={o.adapter} /></TableCell>
                    <TableCell>{o.location}</TableCell>
                    <TableCell align="right" className="num">
                      {(() => { const e = est(o);
                        return e ? `${e.currency} ${e.amount}${e.partial ? '+' : ''}` : '—'; })()}
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
                        {o.mode === 'prototype' && (
                          <Chip size="small" variant="outlined" label="prototype" />
                        )}
                        <Chip size="small" variant="outlined"
                              color={STATUS_COLOR[o.status]}
                              label={o.status.replace('_', ' ')} />
                        {o.status === 'awaiting_payment' && o.pay_url && (
                          <Link href={o.pay_url} target="_blank" rel="noreferrer"
                                onClick={e => e.stopPropagation()} variant="body2">pay</Link>
                        )}
                      </Stack>
                    </TableCell>
                    <TableCell>{o.username}</TableCell>
                    <TableCell className="num">{fmtTime(o.created_at)}</TableCell>
                    {isAdmin && (
                      <TableCell align="right" onClick={e => e.stopPropagation()}>
                        <Stack direction="row" spacing={1} sx={{ justifyContent: 'flex-end' }}>
                          {o.status === 'draft' && (
                            <>
                              <Button size="small" variant="outlined" disabled={actBusy === o.id}
                                      onClick={() => act(o.id, 'confirm')}>Confirm</Button>
                              <Button size="small" color="error" disabled={actBusy === o.id}
                                      onClick={() => act(o.id, 'cancel')}>Cancel</Button>
                            </>
                          )}
                          {o.status === 'confirmed' && (
                            <>
                              <Button size="small" variant="contained" disabled={actBusy === o.id}
                                      color={o.mode === 'real' ? 'warning' : 'primary'}
                                      onClick={() => o.mode === 'real' ? setExecuteReal(o)
                                                                       : act(o.id, 'execute')}>
                                {o.mode === 'real' ? 'Execute (buy)…' : 'Execute'}
                              </Button>
                              <Button size="small" color="error" disabled={actBusy === o.id}
                                      onClick={() => act(o.id, 'cancel')}>Cancel</Button>
                            </>
                          )}
                        </Stack>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}

      <ConfirmDialog
        open={!!executeReal}
        title="Buy at the provider"
        serverName={executeReal?.plan_name ?? ''}
        body={`Execute order #${executeReal?.id}: ${executeReal?.adapter} creates ${executeReal?.plan_name} in ${executeReal?.location} and bills it to the account. This can't be undone from the panel.`}
        confirmLabel="Buy"
        requireTyped
        confirming={actBusy === executeReal?.id}
        onConfirm={() => { const o = executeReal!; setExecuteReal(null); void act(o.id, 'execute'); }}
        onClose={() => setExecuteReal(null)}
      />
      {detailId != null && <OrderDetailDialog orderId={detailId} onClose={() => setDetailId(null)} />}
      <Toast msg={toast} onClose={() => setToast(null)} />
    </Stack>
  );
}

function OrderDetailDialog({ orderId, onClose }: { orderId: number; onClose: () => void }) {
  const o = useQuery({ queryKey: ['order', orderId],
    queryFn: () => api<OrderRow & { events: { status: string; detail: string | null; created_at: string }[] }>(
      `/api/orders/${orderId}`) });
  if (o.isPending) return null;
  if (o.isError) return <Dialog aria-labelledby="omni-dlg-150" open onClose={onClose}><DialogContent>
    <Alert severity="error">{(o.error as Error).message}</Alert></DialogContent></Dialog>;
  const d = o.data!;
  const e_ = est(d);
  return (
    <Dialog aria-labelledby="omni-dlg-155" open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle id="omni-dlg-155">Order #{d.id} · {d.plan_name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
            <Chip size="small" variant="outlined" label={d.mode} />
            <Chip size="small" variant="outlined" label={d.status} />
            <Chip size="small" variant="outlined" label={d.adapter} />
          </Stack>
          <Typography variant="body2" sx={{ color: 'text.secondary' }}>
            {d.location} · estimated {e_ ? `${e_.currency} ${e_.amount}/mo` : '—'}
            {e_?.partial && ' (partial - IP price not published)'}
          </Typography>
          {d.resulting_provider_id && (
            <Typography variant="body2" className="num">
              result: {d.resulting_provider_id}
            </Typography>
          )}
          {d.status === 'awaiting_payment' && (
            <Alert severity="info">
              The provider created order {d.provider_ref} unpaid - nothing is
              delivered or charged until it's paid.{' '}
              {d.pay_url && <Link href={d.pay_url} target="_blank" rel="noreferrer">Pay it at the provider</Link>}
            </Alert>
          )}
          <Box>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Timeline</Typography>
            <Stack spacing={1} sx={{ mt: 0.5 }}>
              {d.events.map((e, i) => (
                <Stack key={i} direction="row" spacing={1.5}
                       sx={{ alignItems: 'baseline' }}>
                  <Typography className="num" variant="caption" sx={{ color: 'text.secondary', width: 64 }}>
                    {fmtTime(e.created_at)}
                  </Typography>
                  <Chip size="small" variant="outlined" label={e.status}
                        color={STATUS_COLOR[e.status]} />
                  {e.detail && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>{e.detail}</Typography>
                  )}
                </Stack>
              ))}
            </Stack>
          </Box>
        </Stack>
      </DialogContent>
    </Dialog>
  );
}
