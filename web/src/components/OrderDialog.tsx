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
import { api, post } from '../api';
import type { Plan } from '../types';

/**
 * Order dialog for a catalog plan. Prototype mode: creates a draft order
 * (auditable, clearly-labeled placeholder execution) - the mode column makes
 * real execution a later flip, and the UI says prototype in plain text.
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
                  image: image || undefined },
      });
      onDone(`Draft order created for ${plan.name}`, 'success');
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
    <Dialog aria-labelledby="omni-dlg-72" open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle id="omni-dlg-72">Order {plan.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Alert severity="info">
            Prototype order flow - no server is created or billed. Orders are
            recorded and can be tracked to completion as a pipeline rehearsal.
          </Alert>
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
          {plan.extra_ip && (
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
                  {new Intl.NumberFormat('en', { style: 'currency', currency: estimate.currency })
                    .format(estimate.total)}
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
          {busy ? 'Placing order…' : 'Create draft order'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
