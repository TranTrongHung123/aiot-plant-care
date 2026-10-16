"""Process inference có timeout cứng; cache model tồn tại giữa các job."""

import atexit
import multiprocessing

from app.modules.trained_ai import InferenceUnavailable


def serve(connection):
    from app.modules.trained_ai import predict

    while True:
        try:
            args, kwargs = connection.recv()
        except EOFError:
            return
        try:
            connection.send((True, predict(*args, **kwargs)))
        except Exception as exc:
            connection.send((False, str(exc)))


class InferenceProcess:
    def __init__(self):
        self.process = None
        self.connection = None

    def close(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=5)
        if self.connection is not None:
            self.connection.close()
        self.process = self.connection = None

    def predict(self, *args, **kwargs):
        if self.process is None or not self.process.is_alive():
            self.close()
            context = multiprocessing.get_context("spawn")
            self.connection, child = context.Pipe()
            self.process = context.Process(target=serve, args=(child,), daemon=True)
            self.process.start()
            child.close()
        try:
            self.connection.send((args, kwargs))
            if not self.connection.poll(120):
                raise TimeoutError("Inference vượt quá 120 giây")
            ok, result = self.connection.recv()
        except (TimeoutError, EOFError, OSError):
            self.close()
            raise
        if not ok:
            raise InferenceUnavailable(result)
        return result


runtime = InferenceProcess()
atexit.register(runtime.close)
