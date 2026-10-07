import { useEffect, useState } from 'react';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import TextField from '@mui/material/TextField';

/**
 * Destructive-action confirmation: names the target, states irreversibility
 * in plain text. For delete, the operator must TYPE the target name - kept
 * annoying on purpose; the operating context lost 8 boxes to a wrong deletion.
 * `children` renders extra controls (e.g. the rebuild image picker).
 */
export function ConfirmDialog({
  open, title, serverName, body, requireTyped, confirming, error,
  confirmDisabled, onConfirm, onClose, confirmLabel, children,
}: {
  open: boolean;
  title: string;
  serverName: string;
  body: string;
  requireTyped?: boolean;
  confirming?: boolean;
  error?: string | null;
  /** extra gate beyond the typed-name check (e.g. rebuild needs a picked image) */
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onClose: () => void;
  confirmLabel?: string;
  children?: React.ReactNode;
}) {
  const [typed, setTyped] = useState('');
  // reset on every open: the typed-name gate must not carry over from a
  // previous destructive action (second dialog would come pre-confirmed)
  useEffect(() => { if (open) setTyped(''); }, [open]);
  const ready = !requireTyped || typed.trim() === serverName;
  const mismatch = requireTyped && typed.trim().length > 0 && !ready;
  const busyLabel = { 'Shut down': 'Shutting down…', Reboot: 'Rebooting…', Rebuild: 'Rebuilding…',
                      Delete: 'Deleting…', Remove: 'Removing…',
                      'Enable purchases': 'Enabling…', 'Change IP': 'Changing…',
                      Release: 'Releasing…', 'Add IP': 'Adding…',
                      'Set password': 'Setting…' }[confirmLabel ?? 'Confirm']
                    ?? `${confirmLabel ?? 'Confirm'}ing…`;
  return (
    <Dialog aria-labelledby="omni-dlg-39" open={open} onClose={confirming ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle id="omni-dlg-39">{title}</DialogTitle>
      <DialogContent>
        <DialogContentText>
          {body}
        </DialogContentText>
        {children}
        {requireTyped && (
          <TextField
            autoFocus
            margin="dense"
            label={`Type "${serverName}" to confirm`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            error={mismatch}
            helperText={mismatch ? "Doesn't match — check spelling" : undefined}
            fullWidth
          />
        )}
        {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={confirming}>Cancel</Button>
        <Button
          onClick={onConfirm}
          color="error"
          variant="contained"
          disabled={!ready || confirming || confirmDisabled}
        >
          {confirming ? busyLabel : (confirmLabel ?? 'Confirm')}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
