import React from 'react';
import Alert from '@mui/material/Alert';
import Snackbar from '@mui/material/Snackbar';
import Stack from '@mui/material/Stack';

export type ToastMsg = { message: string; severity?: 'success' | 'error' | 'info' } | null;

/**
 * Toast surface: a small queue (two rapid toasts both show — the second
 * never silently eats the first), errors stay until dismissed (recovery
 * text must not vanish after 8s), bottom-right so it never covers the
 * pagination footer.
 */
export const Toast = React.forwardRef<HTMLDivElement, {
  msg: ToastMsg;
  onClose: () => void;
}>(function Toast({ msg, onClose }, ref) {
  const [queue, setQueue] = React.useState<NonNullable<ToastMsg>[]>([]);
  const [seen, setSeen] = React.useState<NonNullable<ToastMsg> | null>(null);

  React.useEffect(() => {
    if (msg && msg !== seen) {
      setQueue(q => [...q.slice(-2), msg]); // cap at 3 visible
      setSeen(msg);
    }
  }, [msg, seen]);

  const closeOne = (m: NonNullable<ToastMsg>) => {
    setQueue(q => q.filter(x => x !== m));
    // let the caller clear its source state too
    if (queue.length === 1) onClose();
  };

  if (queue.length === 0) return null;
  return (
    <Stack ref={ref} spacing={1}
           sx={{ position: 'fixed', bottom: 16, right: 16, zIndex: t => t.zIndex.snackbar }}>
      {queue.map((m, i) => (
        <Snackbar
          key={`${m.message}-${i}`}
          open
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          autoHideDuration={m.severity === 'error' ? null : 4000} // errors wait for a click
          onClose={() => closeOne(m)}
          sx={{ position: 'static' }}
        >
          <Alert
            severity={m.severity ?? 'info'}
            onClose={() => closeOne(m)}
            variant="filled"
            sx={{ width: '100%', maxWidth: 420 }}
          >
            {m.message}
          </Alert>
        </Snackbar>
      ))}
    </Stack>
  );
});
