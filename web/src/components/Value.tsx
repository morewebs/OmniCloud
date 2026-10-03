import { Box, Tooltip, Typography } from '@mui/material';

/**
 * The data-honesty workhorse (design.md 6).
 * - value != null -> mono number + optional window/source label
 * - field in not_exposed -> "not exposed" in muted text
 * - value == null otherwise -> "-" with a "waiting for sync" tooltip
 * Never renders zero for either unavailability case.
 */
export function Value({
  value, format, notExposed, window: windowLabel, pendingHint,
}: {
  value: number | string | null | undefined;
  format?: (v: number | string) => string;
  notExposed?: boolean;
  window?: string | null;
  pendingHint?: string;
}) {
  if (notExposed) {
    return (
      <Typography variant="body2" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
        not exposed
      </Typography>
    );
  }
  if (value == null) {
    return (
      <Tooltip title={pendingHint ?? 'Waiting for the next sync from this provider'}>
        <Typography component="span" variant="body2" sx={{ color: 'text.secondary' }}>
          —
        </Typography>
      </Tooltip>
    );
  }
  const shown = format ? format(value) : String(value);
  return (
    <Box>
      <Typography component="span" className="num">{shown}</Typography>
      {windowLabel && (
        <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
          {windowLabel}
        </Typography>
      )}
    </Box>
  );
}

/** Stale data decays visibly: dimmed + "as of HH:MM" instead of presenting as live. */
export function StaleStamp({ lastSeenAt, intervalMinutes, children }: {
  lastSeenAt?: string; intervalMinutes?: number; children: React.ReactNode;
}) {
  if (!lastSeenAt || !intervalMinutes) return <>{children}</>;
  const ageMin = (Date.now() - new Date(lastSeenAt).getTime()) / 60_000;
  if (ageMin <= intervalMinutes * 2 + 1) return <>{children}</>;
  return (
    <Tooltip title={`This value is older than the expected sync interval (${intervalMinutes} min)`}>
      <Box sx={{ opacity: 0.55 }}>
        {children}
        <Typography variant="caption" sx={{ color: 'text.secondary', ml: 1 }}>
          as of {new Date(lastSeenAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </Typography>
      </Box>
    </Tooltip>
  );
}
