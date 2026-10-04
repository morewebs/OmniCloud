import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControlLabel from '@mui/material/FormControlLabel';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { api, fmtBytes, post } from '../api';
import { PageHeader } from '../components/PageHeader';
import { EmptyState } from '../components/EmptyState';
import { OrderDialog } from '../components/OrderDialog';
import { usePageTitle } from '../usePageTitle';
import type { Plan } from '../types';
import { Toast } from '../components/Toast';
import type { ToastMsg } from '../components/Toast';

interface CatalogPayload {
  providers: { key: string; display_name: string; source: string; capabilities: string[] }[];
  plans: Record<string, (Plan & { source: string; last_verified: string | null })[]>;
  state: { adapter: string; last_success_at: string | null; last_error: string | null }[];
}

export function CatalogView() {
  usePageTitle('Catalog');
  const qc = useQueryClient();
  const cat = useQuery<CatalogPayload>({ queryKey: ['catalog'],
    queryFn: () => api<CatalogPayload>('/api/catalog') });
  const me = useQuery({ queryKey: ['me'],
    queryFn: () => api<{ role: string }>('/api/auth/me') });
  const isAdmin = me.data?.role === 'admin';

  const [adapterF, setAdapterF] = useState('all');
  const [locF, setLocF] = useState('all');
  const [maxPrice, setMaxPrice] = useState('');
  const [minTraffic, setMinTraffic] = useState('');
  const [ipOnly, setIpOnly] = useState(false);
  const [orderPlan, setOrderPlan] = useState<(Plan & { source: string }) | null>(null);
  const [compare, setCompare] = useState<string[]>([]); // "adapter|name|location" keys
  const [toast, setToast] = useState<ToastMsg>(null);

  const allPlans = useMemo(() => {
    const rows: (Plan & { source: string; last_verified?: string | null })[] = [];
    for (const plans of Object.values(cat.data?.plans ?? {})) {
      for (const p of plans) rows.push(p);
    }
    return rows;
  }, [cat.data]);

  const locations = useMemo(
    () => [...new Set(allPlans.map(p => p.location))].sort(), [allPlans]);

  const rows = useMemo(() => allPlans.filter(p => {
    if (adapterF !== 'all' && p.adapter !== adapterF) return false;
    if (locF !== 'all' && p.location !== locF) return false;
    if (ipOnly && !p.extra_ip) return false;
    // NaN from free-text ("abc", "12,5") would make every comparison false and
    // silently pass ALL rows - guard with isFinite
    const maxN = Number(maxPrice);
    const minN = Number(minTraffic);
    if (Number.isFinite(maxN) && maxN > 0 && p.price_monthly
        && Number(p.price_monthly.amount) > maxN) return false;
    if (Number.isFinite(minN) && minN > 0
        && (p.included_traffic_bytes ?? 0) < minN * 1e9) return false;
    return true;
  }), [allPlans, adapterF, locF, ipOnly, maxPrice, minTraffic]);

  const [syncing, setSyncing] = useState(false);
  const syncNow = async () => {
    setSyncing(true);
    try {
      await post('/api/catalog/sync');
      qc.invalidateQueries({ queryKey: ['catalog'] });
      setToast({ message: 'Catalog refreshed', severity: 'success' });
    } catch (e) {
      setToast({ message: (e as Error).message, severity: 'error' });
    } finally {
      setSyncing(false);
    }
  };

  if (cat.isPending) return <Typography sx={{ color: 'text.secondary' }}>Loading…</Typography>;
  if (cat.isError) return <Alert severity="error">{(cat.error as Error).message}</Alert>;

  return (
    <Stack spacing={3}>
      <PageHeader
        title="Catalog"
        subtitle="Plans across providers with declared traffic and extra-IP capability."
        actions={isAdmin && <Button variant="contained" disabled={syncing} onClick={syncNow}>
          {syncing ? 'Refreshing…' : 'Refresh catalog'}</Button>}
      />

      <Stack direction="row" spacing={1.5} sx={{ flexWrap: 'wrap', gap: 1.5, alignItems: 'center' }}>
        <TextField select size="small" label="Provider" value={adapterF}
                   onChange={e => setAdapterF(e.target.value)} sx={{ minWidth: 130 }}>
          <MenuItem value="all">All providers</MenuItem>
          {Object.keys(cat.data.plans).map(a => <MenuItem key={a} value={a}>{a}</MenuItem>)}
        </TextField>
        <TextField select size="small" label="Location" value={locF}
                   onChange={e => setLocF(e.target.value)} sx={{ minWidth: 120 }}>
          <MenuItem value="all">All locations</MenuItem>
          {locations.map(l => <MenuItem key={l} value={l}>{l}</MenuItem>)}
        </TextField>
        <TextField size="small"
                   label={new Set(allPlans.map(p => p.price_monthly?.currency)).size > 1
                     ? 'Max price/mo (per currency)' : 'Max price/mo'}
                   value={maxPrice} className="num"
                   onChange={e => setMaxPrice(e.target.value)} sx={{ width: 100 }}
                   placeholder="any" />
        <TextField size="small" label="Min TB traffic" value={minTraffic} className="num"
                   onChange={e => setMinTraffic(e.target.value)} sx={{ width: 130 }}
                   placeholder="any" />
        <FormControlLabel control={<Checkbox checked={ipOnly} onChange={e => setIpOnly(e.target.checked)} />}
                         label={<Typography variant="body2">Extra IPs offered</Typography>} />
        <Box sx={{ flex: 1 }} />
        {compare.length > 0 && (
          <Button variant="outlined" size="small" onClick={() => setCompare([])}>
            Clear compare ({compare.length})
          </Button>
        )}
      </Stack>

      {rows.length === 0
        ? <EmptyState mark="catalog" line="No plans match the filters."
                      actionLabel="Reset filters"
                      onAction={() => { setAdapterF('all'); setLocF('all'); setMaxPrice('');
                                        setMinTraffic(''); setIpOnly(false); }} />
        : (
          <>
          {rows.length > 200 && (
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              showing the first 200 of {rows.length} matching plans — narrow the filters
            </Typography>
          )}
          <Box sx={{ overflowX: 'auto', maxHeight: 620 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox" />
                  <TableCell>Plan</TableCell>
                  <TableCell>Provider</TableCell>
                  <TableCell>Location</TableCell>
                  <TableCell>Cores</TableCell>
                  <TableCell>RAM</TableCell>
                  <TableCell>Disk</TableCell>
                  <TableCell align="right">Monthly</TableCell>
                  <TableCell align="right">Traffic incl.</TableCell>
                  <TableCell>Extra IPs</TableCell>
                  <TableCell>Billing</TableCell>
                  <TableCell>Source</TableCell>
                  {isAdmin && <TableCell align="right">Order</TableCell>}
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.slice(0, 200).map(p => {
                  const key = `${p.adapter}|${p.name}|${p.location}`;
                  const inCompare = compare.includes(key);
                  return (
                    <TableRow key={key} hover>
                      <TableCell padding="checkbox">
                        <Checkbox size="small" checked={inCompare}
                          aria-label={`Compare ${p.name}`}
                          disabled={!inCompare && compare.length >= 4}
                          onChange={() => setCompare(c =>
                            inCompare ? c.filter(x => x !== key) : [...c, key])} />
                      </TableCell>
                      <TableCell sx={{ fontWeight: 500 }}>{p.name}</TableCell>
                      <TableCell><Chip size="small" variant="outlined" label={p.adapter} /></TableCell>
                      <TableCell>{p.location}</TableCell>
                      <TableCell className="num">{p.cpu_cores ?? '—'}</TableCell>
                      <TableCell className="num">{p.ram_gb ? `${p.ram_gb} GB` : '—'}</TableCell>
                      <TableCell className="num">{p.disk_gb ? `${p.disk_gb} GB` : '—'}</TableCell>
                      <TableCell align="right" className="num">
                        {p.price_monthly
                          ? new Intl.NumberFormat('en', { style: 'currency',
                              currency: p.price_monthly.currency, maximumFractionDigits: 2 })
                              .format(Number(p.price_monthly.amount))
                          : <Box component="span" sx={{ color: 'text.secondary', fontStyle: 'italic'  }}>not published</Box>}
                      </TableCell>
                      <TableCell align="right" className="num">
                        {p.included_traffic_bytes != null ? fmtBytes(p.included_traffic_bytes)
                          : p.traffic_note
                            ? <Box component="span" sx={{ color: 'text.secondary'  }}>{p.traffic_note}</Box>
                            : <Box component="span" sx={{ color: 'text.secondary'  }}>per account / not published</Box>}
                      </TableCell>
                      <TableCell>
                        {p.extra_ip
                          ? (p.extra_ip.price
                              ? `+${p.extra_ip.price.currency} ${p.extra_ip.price.amount}/mo each`
                              : <Tooltip title={p.extra_ip.note ?? 'price not published'}>
                                  <Box component="span" sx={{ color: 'text.secondary'  }}>offered · price on request</Box>
                                </Tooltip>)
                          : <Box component="span" sx={{ color: 'text.secondary'  }}>—</Box>}
                      </TableCell>
                      <TableCell>{p.billing_model || '—'}</TableCell>
                      <TableCell>
                        {p.source === 'live'
                          ? <Chip size="small" variant="outlined" color="success" label="live" />
                          : <Chip size="small" variant="outlined" label={`curated · ${p.last_verified ?? ''}`} />}
                      </TableCell>
                      {isAdmin && (
                        <TableCell align="right">
                          <Button size="small" variant="outlined" disabled={p.deprecated}
                                  onClick={() => setOrderPlan(p)}>Order…</Button>
                        </TableCell>
                      )}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Box>
          </>
        )}

      {compare.length > 1 && (
        <CompareDialog
          // a catalog refetch can vanish a checked plan mid-compare -
          // drop missing keys instead of crashing render on undefined
          plans={compare.flatMap(k => allPlans
            .filter(p => `${p.adapter}|${p.name}|${p.location}` === k))}
          onClose={() => setCompare([])}
        />
      )}

      {orderPlan && (
        <OrderDialog
          plan={orderPlan}
          onClose={() => setOrderPlan(null)}
          onDone={(msg, sev) => { setOrderPlan(null); setToast({ message: msg, severity: sev });
                                 qc.invalidateQueries({ queryKey: ['orders'] }); }}
        />
      )}
      <Toast msg={toast} onClose={() => setToast(null)} />
    </Stack>
  );
}

function CompareDialog({ plans, onClose }: { plans: Plan[]; onClose: () => void }) {
  return (
    <Dialog aria-labelledby="omni-dlg-247" open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle id="omni-dlg-247">Compare plans</DialogTitle>
      <DialogContent>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell />
              {plans.map(p => <TableCell key={`${p.adapter}${p.name}`} sx={{ fontWeight: 600 }}>
                {p.name}<Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
                  {p.adapter} · {p.location}</Typography>
              </TableCell>)}
            </TableRow>
          </TableHead>
          <TableBody>
            {([
              ['Monthly', (p: Plan) => p.price_monthly
                ? `${p.price_monthly.currency} ${p.price_monthly.amount}` : 'not published'],
              ['Traffic included', (p: Plan) => p.included_traffic_bytes != null ? fmtBytes(p.included_traffic_bytes) : (p.traffic_note ?? 'per account / not published')],
              ['Cores', (p: Plan) => p.cpu_cores ?? '—'],
              ['RAM', (p: Plan) => p.ram_gb ? `${p.ram_gb} GB` : '—'],
              ['Disk', (p: Plan) => p.disk_gb ? `${p.disk_gb} GB` : '—'],
              ['Extra IPs', (p: Plan) => p.extra_ip
                ? (p.extra_ip.price ? `offered · ${p.extra_ip.price.amount}/mo` : 'offered · price on request')
                : '—'],
              ['Billing', (p: Plan) => p.billing_model || '—'],
            ] as const).map(([label, get]) => (
              <TableRow key={label}>
                <TableCell sx={{ color: 'text.secondary' }}>{label}</TableCell>
                {plans.map(p => <TableCell key={`${p.adapter}${p.name}${label}`} className="num">
                  {get(p)}
                </TableCell>)}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DialogContent>
    </Dialog>
  );
}
