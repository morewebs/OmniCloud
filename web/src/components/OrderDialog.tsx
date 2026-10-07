import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { api, fmtCurrency, post } from '../api';
import type { AccountRow, AdapterInfo, Plan } from '../types';

/**
 * Order dialog for a catalog plan. The order is REAL when it names an
 * account of this provider whose purchases are enabled (and the provider's
 * server ordering is wired); otherwise it is a prototype rehearsal that
 * creates nothing. The dialog says which, in plain text, before submit.
 */
const HOSTNAME_RE = /^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/;

export function OrderDialog({ plan, onClose, onDone }: {
  plan: Plan & { source?: string };
  onClose: () => void;
  onDone: (msg: string, severity?: 'success' | 'error') => void;
}) {
  const [hostname, setHostname] = useState('');
  const [extraIps, setExtraIps] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imgFilter, setImgFilter] = useState('');
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });
  const adapters = useQuery<AdapterInfo[]>({ queryKey: ['adapters'],
    queryFn: () => api<AdapterInfo[]>('/api/adapters') });
  const mine = (accounts.data ?? []).filter(a => a.adapter === plan.adapter);
  const [accountId, setAccountId] = useState<number | ''>('');
  const effAccount = accountId === '' ? (mine.length === 1 ? mine[0].id : '') : accountId;
  const acct = mine.find(a => a.id === effAccount);
  const canOrder = !!adapters.data?.find(a => a.key === plan.adapter)?.orders;
  const real = !!acct?.purchases_enabled && canOrder;

  const images = useQuery<{ id: string; name: string; os: string; version: string | null }[]>({
    queryKey: ['catalog-images', plan.adapter],
    queryFn: () => api(`/api/catalog/images?adapter=${plan.adapter}`),
    enabled: plan.source === 'live',
  });
  const [image, setImage] = useState('');

  const estimate = useMemo(() => {
    const base = plan.price_monthly ? Number(plan.price_monthly.amount) : null;
    const ipPrice = plan.extra_ip?.price ? Number(plan.extra_ip.price.amount) : null;
    if (base == null) return null;
    const total = base + (ipPrice != null ? ipPrice * extraIps : 0);
    return { total, currency: plan.price_monthly!.currency,
             partial: ipPrice == null && extraIps > 0 };
  }, [plan, extraIps]);

  const hostnameBad = hostname.length > 0 && !HOSTNAME_RE.test(hostname);

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await post('/api/orders', {
        adapter: plan.adapter, plan_name: plan.name, location: plan.location,
        options: { hostname: hostname || undefined, extra_ips: extraIps,
                  image: image || undefined,
                  account_id: effAccount === '' ? undefined : effAccount },
      });
      onDone(real ? `Draft REAL order created for ${plan.name} - confirm and execute it under Orders`
                  : `Draft order created for ${plan.name}`, 'success');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const imgList = (images.data ?? [])
    .filter(i => !imgFilter
      || `${i.name} ${i.version ?? ''} ${i.os}`.toLowerCase().includes(imgFilter.toLowerCase()));

  return (
    <Dialog aria-labelledby="omni-dlg-72" open onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle id="omni-dlg-72">Order {plan.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {mine.length > 0 && (
            <TextField select label="Account" size="small" value={effAccount}
                       onChange={e => setAccountId(Number(e.target.value))}
                       helperText={!canOrder
                         ? `${plan.adapter} server ordering isn't wired in the panel - orders stay prototype`
                         : acct && !acct.purchases_enabled
                           ? 'purchases are off for this account - the order stays a prototype'
                           : undefined}>
              {mine.map(a => (
                <MenuItem key={a.id} value={a.id}>
                  {a.name}{a.purchases_enabled ? ' · purchases on' : ''}
                </MenuItem>
              ))}
            </TextField>
          )}
          {real ? (
            <Alert severity="warning">
              Real order: executing it buys this server{extraIps > 0 ? ` and ${extraIps} extra IP(s)` : ''} at
              {' '}{plan.adapter} through account {acct!.name}. Providers that bill per order
              (OVH) create an unpaid order you pay at their link.
            </Alert>
          ) : (
            <Alert severity="info">
              Prototype order - no server is created or billed. Orders are
              recorded and can be tracked to completion as a pipeline rehearsal.
            </Alert>
          )}
          {plan.source === 'seeded' && (
            <Alert severity="warning">
              Curated plan data - verify the price with the provider before
              any real purchase.
            </Alert>
          )}
          <TextField label="Hostname" value={hostname} size="small"
                     onChange={e => setHostname(e.target.value)}
                     error={hostnameBad}
                     helperText={hostnameBad
                       ? 'letters, digits and hyphens; cannot start or end with a hyphen'
                       : 'optional — the provider assigns one if left empty'}
                     placeholder="e.g. srv-new-01" />
          {plan.source === 'live' && (
            images.isPending ? (
              <TextField select disabled label="Image" value="" size="small"
                         helperText="Loading images…">
                <MenuItem value="">Loading images…</MenuItem>
              </TextField>
            ) : images.isError ? (
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                  Images unavailable — the provider's default will be used.
                </Typography>
                <Button size="small" onClick={() => images.refetch()}>Retry</Button>
              </Stack>
            ) : (images.data?.length ?? 0) > 0 && (
              <Stack spacing={0.5}>
                {(images.data?.length ?? 0) > 12 && (
                  <TextField size="small" label="Filter images" value={imgFilter}
                             onChange={e => setImgFilter(e.target.value)}
                             sx={{ maxWidth: 260 }} />
                )}
                <TextField select label="Image" value={image} size="small"
                           onChange={e => setImage(e.target.value)}
                           helperText={image ? undefined : 'the provider\'s default image is used if unset'}>
                  {imgList.map(img => (
                    <MenuItem key={img.id} value={img.id}>
                      {img.name} {img.version ? `(${img.version})` : ''} — {img.os}
                    </MenuItem>
                  ))}
                </TextField>
              </Stack>
            )
          )}
          {plan.extra_ip && plan.extra_ip.limit !== 0 && (
            <TextField select label="Extra IPs" value={String(extraIps)} size="small"
                       onChange={e => setExtraIps(Number(e.target.value))}
                       helperText={plan.extra_ip.price
                         ? undefined
                         : `IP price not published — the estimate stays partial`}>
              {Array.from({ length: Math.min(plan.extra_ip.limit ?? 4, 5) + 1 },
                (_, i) => <MenuItem key={i} value={i} className="num">{i}</MenuItem>)}
            </TextField>
          )}
          {estimate && (
            <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>Estimated monthly</Typography>
              <Stack sx={{ textAlign: 'right' }}>
                <Typography className="num" variant="body2" sx={{ fontWeight: 600 }}>
                  {fmtCurrency(estimate.total, estimate.currency)}
                </Typography>
                {estimate.partial && (
                  <Typography variant="caption" sx={{ color: 'warning.main' }}>
                    + IP prices not published by this provider
                  </Typography>
                )}
              </Stack>
            </Stack>
          )}
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" disabled={busy || hostnameBad} onClick={submit}>
          {busy ? 'Placing order…' : real ? 'Create draft real order' : 'Create draft order'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
