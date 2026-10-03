import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
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
import { api, post } from '../api';
import { PageHeader } from '../components/PageHeader';
import { EmptyState } from '../components/EmptyState';
import { Toast } from '../components/Toast';
import { usePageTitle } from '../usePageTitle';
import type { ToastMsg } from '../components/Toast';
import type { OrderRow } from '../types';

const STATUS_COLOR: Record<string, 'success' | 'error' | 'warning' | undefined> = {
  provisioned: 'success', failed: 'error', executing: 'warning',
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

  const act = async (id: number, verb: string) => {
    try {
      await post(`/api/orders/${id}/${verb}`);
      qc.invalidateQueries({ queryKey: ['orders'] });
      qc.invalidateQueries({ queryKey: ['overview'] });
      setToast({ message: `Order ${verb}ed`, severity: 'success' });
    } catch (e) {
      setToast({ message: (e as Error).message, severity: 'error' });
    }
  };

  if (orders.isPending) return <Typography sx={{ color: 'text.secondary' }}>Loading…</Typography>;
  if (orders.isError) return <Alert severity="error">{(orders.error as Error).message}</Alert>;

  return (
    <Stack spacing={3}>
      <PageHeader title="Orders"
                  subtitle="Prototype pipeline: draft, confirm, execute. No servers are created or billed." />

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
                  <TableRow key={o.id} hover onClick={() => setDetailId(o.id)}
                            sx={{ cursor: 'pointer' }}>
                    <TableCell className="num">{o.id}</TableCell>
                    <TableCell sx={{ fontWeight: 500 }}>{o.plan_name}</TableCell>
                    <TableCell><Chip size="small" variant="outlined" label={o.adapter} /></TableCell>
                    <TableCell>{o.location}</TableCell>
                    <TableCell align="right" className="num">
                      {(() => { const e = JSON.parse(o.estimated_monthly);
                        return `${e.currency} ${e.amount}${e.partial ? '+' : ''}`; })()}
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
                        {o.mode === 'prototype' && (
                          <Chip size="small" variant="outlined" label="prototype" />
                        )}
                        <Chip size="small" variant="outlined"
                              color={STATUS_COLOR[o.status]}
                              label={o.status} />
                      </Stack>
                    </TableCell>
                    <TableCell>{o.username}</TableCell>
                    <TableCell className="num">{o.created_at.slice(0, 16).replace('T', ' ')}</TableCell>
                    {isAdmin && (
                      <TableCell align="right" onClick={e => e.stopPropagation()}>
                        <Stack direction="row" spacing={1} sx={{ justifyContent: 'flex-end' }}>
                          {o.status === 'draft' && (
                            <>
                              <Button size="small" variant="outlined"
                                      onClick={() => act(o.id, 'confirm')}>Confirm</Button>
                              <Button size="small" color="error"
                                      onClick={() => act(o.id, 'cancel')}>Cancel</Button>
                            </>
                          )}
                          {o.status === 'confirmed' && (
                            <>
                              <Button size="small" variant="contained"
                                      onClick={() => act(o.id, 'execute')}>Execute</Button>
                              <Button size="small" color="error"
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
  if (o.isError) return <Dialog open onClose={onClose}><DialogContent>
    <Alert severity="error">{(o.error as Error).message}</Alert></DialogContent></Dialog>;
  const d = o.data!;
  const est = JSON.parse(d.estimated_monthly);
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Order #{d.id} · {d.plan_name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2}>
          <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
            <Chip size="small" variant="outlined" label={d.mode} />
            <Chip size="small" variant="outlined" label={d.status} />
            <Chip size="small" variant="outlined" label={d.adapter} />
          </Stack>
          <Typography variant="body2" sx={{ color: 'text.secondary' }}>
            {d.location} · estimated {est.currency} {est.amount}/mo
            {est.partial && ' (partial - IP price not published)'}
          </Typography>
          {d.resulting_provider_id && (
            <Typography variant="body2" className="num">
              result: {d.resulting_provider_id}
            </Typography>
          )}
          <Box>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Timeline</Typography>
            <Stack spacing={1} sx={{ mt: 0.5 }}>
              {d.events.map((e, i) => (
                <Stack key={i} direction="row" spacing={1.5}
                       sx={{ alignItems: 'baseline' }}>
                  <Typography className="num" variant="caption" sx={{ color: 'text.secondary', width: 64 }}>
                    {e.created_at.slice(11, 16)}
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
