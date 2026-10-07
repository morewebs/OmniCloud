import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Link from '@mui/material/Link';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { api, del, fmtMoney, post } from '../api';
import type { IpCost, Server } from '../types';
import { IP_COST_PER } from '../types';
import { ConfirmDialog } from './ConfirmDialog';

// provider IP work (order, attach, confirm on the provider's own view) can
// take minutes - the routes are synchronous, so the client waits for them
const IP_TIMEOUT_MS = 10 * 60_000;

interface IpInfo {
  cost: IpCost | null;
  purchases_enabled: boolean;
  acquisitions_last_24h: number;
  daily_cap: number;
}

interface IpResult {
  status: 'done' | 'awaiting_payment';
  old_ip?: string; new_ip?: string; old_released?: boolean;
  warning?: string; pay_url?: string | null; provider_ref?: string;
}

export function costText(c: IpCost | null | undefined): string {
  if (!c) return 'cost not published';
  return c.price ? `${fmtMoney(c.price)} ${IP_COST_PER[c.per]}`
                 : `charged ${IP_COST_PER[c.per]}, price not published`;
}

/** A server's public IPs: the primary (never touched) and the swappable
 *  extras, with change / release / add where the adapter declares them. */
export function IpSection({ server, isAdmin, capabilities, onDone }: {
  server: Server; isAdmin: boolean; capabilities: string[]; onDone: () => void;
}) {
  const has = (c: string) => capabilities.includes(c); // absent = not rendered
  const ips = server.ips ?? [];
  const probe = ips[0]?.address ?? server.ipv4;
  const info = useQuery<IpInfo>({
    queryKey: ['ip', probe],
    queryFn: () => api<IpInfo>(`/api/ips/${probe}`),
    enabled: isAdmin && !!probe && (has('ip_add') || has('ip_change')),
  });
  const [confirm, setConfirm] = useState<{ kind: 'change' | 'release' | 'add'; ip?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IpResult | null>(null);

  if (!ips.length && !server.ipv4) return null;
  const purchases = info.data?.purchases_enabled ?? false;
  const cost = costText(info.data?.cost);

  const run = async () => {
    if (!confirm) return;
    setBusy(true); setError(null); setResult(null);
    const base = `/api/servers/${server.account_id}/${encodeURIComponent(server.provider_id)}/ips`;
    try {
      if (confirm.kind === 'change')
        setResult(await post<IpResult>(`/api/ips/${confirm.ip}/change`, {}, IP_TIMEOUT_MS));
      else if (confirm.kind === 'add')
        setResult(await post<IpResult>(base, {}, IP_TIMEOUT_MS));
      else
        await del(`${base}/${confirm.ip}`, IP_TIMEOUT_MS);
      onDone();
      void info.refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false); setConfirm(null);
    }
  };

  const rows = ips.length ? ips
    : [{ address: server.ipv4!, version: 4, primary: true, kind: 'primary',
         provider_ip_id: null, monthly_price: null }];
  const canAct = isAdmin && purchases;

  return (
    <Stack spacing={1}>
      <Typography variant="overline" color="text.secondary">IP addresses</Typography>
      <Table size="small">
        <TableBody>
          {rows.map(ip => (
            <TableRow key={ip.address}>
              <TableCell className="num">{ip.address}</TableCell>
              <TableCell>
                <Chip size="small" variant="outlined"
                      label={ip.primary ? 'primary' : `swappable · ${ip.kind}`} />
              </TableCell>
              <TableCell align="right">
                {!ip.primary && canAct && has('ip_change') && (
                  <Button size="small" disabled={busy}
                          onClick={() => setConfirm({ kind: 'change', ip: ip.address })}>
                    Change…
                  </Button>
                )}
                {!ip.primary && isAdmin && has('ip_release') && (
                  <Button size="small" color="error" disabled={busy}
                          onClick={() => setConfirm({ kind: 'release', ip: ip.address })}>
                    Release…
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {isAdmin && has('ip_add') && (
        purchases
          ? <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              <Button size="small" variant="outlined" disabled={busy}
                      onClick={() => setConfirm({ kind: 'add' })}>Add IP…</Button>
              <Typography variant="caption" color="text.secondary">
                {cost} · {info.data?.acquisitions_last_24h ?? '—'}/{info.data?.daily_cap ?? '—'} acquisitions in the last 24 h
              </Typography>
            </Stack>
          : info.isSuccess && (
            <Typography variant="caption" color="text.secondary">
              Buying or changing IPs is off for this account - an admin enables
              purchases under Credentials.
            </Typography>
          )
      )}
      {result?.status === 'done' && (
        <Alert severity={result.warning ? 'warning' : 'success'}>
          {result.old_ip
            ? <><span className="num">{result.old_ip}</span> → <span className="num">{result.new_ip}</span></>
            : <>Added <span className="num">{result.new_ip}</span></>}
          {result.warning && <> - {result.warning}</>}.
          {' '}Configure the new address on the server if your provider needs that.
        </Alert>
      )}
      {result?.status === 'awaiting_payment' && (
        <Alert severity="info">
          The provider created an unpaid order ({result.provider_ref}); the IP is
          delivered after payment.{' '}
          {result.pay_url && <Link href={result.pay_url} target="_blank" rel="noreferrer">Pay the order</Link>}
        </Alert>
      )}
      {error && <Alert severity="error">{error}</Alert>}

      <ConfirmDialog
        open={confirm?.kind === 'change'}
        title="Change IP"
        serverName={confirm?.ip ?? ''}
        body={`Acquire a new IP on ${server.name}, then release ${confirm?.ip}. Cost: ${cost}. The provider picks the new address - it can't be previewed.`}
        confirmLabel="Change IP"
        requireTyped
        confirming={busy}
        onConfirm={() => void run()}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.kind === 'release'}
        title="Release IP"
        serverName={confirm?.ip ?? ''}
        body={`Give ${confirm?.ip} back to the provider. The address is gone for good - anything pointing at it stops working.`}
        confirmLabel="Release"
        requireTyped
        confirming={busy}
        onConfirm={() => void run()}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.kind === 'add'}
        title="Add IP"
        serverName={server.name}
        body={`Buy one more public IPv4 for ${server.name}. Cost: ${cost}.`}
        confirmLabel="Add IP"
        requireTyped
        confirming={busy}
        onConfirm={() => void run()}
        onClose={() => setConfirm(null)}
      />
    </Stack>
  );
}
