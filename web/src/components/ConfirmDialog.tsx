import { useState } from 'react';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import TextField from '@mui/material/TextField';

/**
 * Destructive-action confirmation: names the server, states irreversibility
 * in plain text. For delete, the operator must TYPE the server name - kept
 * annoying on purpose; the operating context lost 8 boxes to a wrong deletion.
 */
export function ConfirmDialog({
  open, title, serverName, body, requireTyped, confirming, error, onConfirm, onClose,
}: {
  open: boolean;
  title: string;
  serverName: string;
  body: string;
  requireTyped?: boolean;
  confirming?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [typed, setTyped] = useState('');
  const ready = !requireTyped || typed === serverName;
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <DialogContentText>
          {body}
        </DialogContentText>
        {requireTyped && (
          <TextField
            autoFocus
            margin="dense"
            label={`Type "${serverName}" to confirm`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            fullWidth
          />
        )}
        {error && (
          <DialogContentText color="error" sx={{ mt: 1 }}>
            {error}
          </DialogContentText>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          onClick={onConfirm}
          color="error"
          variant="contained"
          disabled={!ready || confirming}
        >
          {confirming ? 'Working…' : 'Confirm'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
