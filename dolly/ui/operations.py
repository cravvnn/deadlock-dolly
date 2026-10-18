"""Single-worker operation queue. No Tk, controller or export dependencies."""
import logging
import queue
import threading

EVENT_QUEUE_LIMIT = 4096
LOG = logging.getLogger('dolly')


class OperationQueue:
    def __init__(self):
        self.events = queue.Queue(maxsize=EVENT_QUEUE_LIMIT)
        self.jobs = queue.Queue()
        self.dropped_logs = 0
        self.closed = False
        self.busy = False
        self.worker = None

    def start(self, failure_kind):
        if self.worker is not None:
            raise RuntimeError('The operation worker has already been started.')
        self.worker = threading.Thread(target=self.run, args=(failure_kind,),
                                       name='dolly-ui-operations', daemon=True)
        self.worker.start()
        return self.worker

    def submit(self, label, function, callback=None, *, blocked=False, started, rejected):
        if self.closed:
            return False
        if blocked:
            rejected('Finish or cancel Bone Picker before starting another operation.')
            return False
        if self.busy:
            rejected('Please wait for the current operation to finish.')
            return False
        self.busy = True
        started(label)
        self.jobs.put((label, function, callback))
        return True

    def run(self, failure_kind):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            label, function, callback = job
            try:
                result = function()
            except Exception as exc:
                from ..controller import PreloadUnavailableError
                if isinstance(exc, PreloadUnavailableError):
                    # The desktop offers its manual replay load for this, so it is
                    # an expected outcome on this platform, not a failure to trace.
                    LOG.info('Operation needs the manual step: %s: %s', label, exc)
                else:
                    LOG.exception('Operation failed: %s', label)
                try:
                    kind = failure_kind()
                except Exception:
                    LOG.exception('Operation failure recovery failed; retaining the worker')
                    kind = 'error'
                self.events.put((kind, label, exc))
            else:
                self.events.put(('done', label, (result, callback)))

    def enqueue_log(self, *parts):
        try:
            self.events.put_nowait(('log', '', ' '.join(str(part) for part in parts)))
        except queue.Full:
            self.dropped_logs += 1

    def pending_events(self, limit=100):
        """Drain lazily on the caller/UI thread, preserving callback ordering."""
        for _ in range(limit):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            yield event

    def shutdown(self):
        self.jobs.put(None)
