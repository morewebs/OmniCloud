import React from 'react';
import Alert from '@mui/material/Alert';
import Snackbar from '@mui/material/Snackbar';

export type ToastMsg = { message: string; severity?: 'success' | 'error' | 'info' } | null;

/** Minimal toast surface: one state + setter per app, no library. */
export const Toast = React.forwardRef(function Toast(
  { msg, onClose }: { msg: ToastMsg; onClose: () => void },
  _ref: React.Ref<unknown>,
) {
  return (
    <Snackbar
      open={!!msg}
      autoHideDuration={msg?.severity === 'error' ? 8000 : 4000}
      onClose={onClose}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
    >
      {msg ? (
        <Alert
          severity={msg.severity ?? 'info'}
          onClose={onClose}
          variant="filled"
          sx={{ width: '100%' }}
        >
          {msg.message}
        </Alert>
      ) : undefined}
    </Snackbar>
  );
});
