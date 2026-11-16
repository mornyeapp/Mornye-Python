from __future__ import annotations

import threading

from mornye.app import MornyeAPI


def test_stop_all_signals_the_active_download(monkeypatch) -> None:
    api = MornyeAPI()
    entered = threading.Event()
    stopped = threading.Event()

    def run_batch(*_args, stop_event, **_kwargs) -> None:
        entered.set()
        assert stop_event.wait(timeout=2)
        stopped.set()

    monkeypatch.setattr(api, "_run_download_batch", run_batch)
    worker = threading.Thread(target=api._download_task, args=([], {}))
    worker.start()
    assert entered.wait(timeout=2)

    result = api.stop_all_downloads()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert result == {"ok": True, "stopped": 1, "cancelled_pending": 0}
    assert stopped.is_set()
