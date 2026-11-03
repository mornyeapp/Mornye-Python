"""Live extension progress must survive options and the Node reader thread."""

import asyncio
from pathlib import Path
import shutil

import pytest

from mornye.extensions.provider import JSExtensionProvider
from mornye.extensions.runtime import JSRuntime


@pytest.mark.parametrize("with_options", [False, True])
def test_real_node_callback_is_registered_with_download_options(tmp_path, with_options):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node required for extension integration")
    extension = tmp_path / "index.js"
    extension.write_text("""
registerExtension({
  download: function(id, quality, path, onProgress, options) {
    if (typeof onProgress !== 'function') throw new Error('missing progress callback');
    onProgress(0.25);
    onProgress(0.75);
    return {marker: options ? options.marker : null};
  }
});
""")
    runtime = JSRuntime(extension, node_executable=node)
    seen = []
    args = ["track", "LOSSLESS", "/tmp/unused", None]
    if with_options:
        args.append({"marker": "preserved"})
    runtime.start()
    try:
        result = runtime.call("download", *args, progress_cb=seen.append, timeout=5)
    finally:
        runtime.stop()
    assert seen == [0.25, 0.75]
    assert result["marker"] == ("preserved" if with_options else None)


def test_chunked_transfer_preserves_received_bytes_without_a_total():
    runtime = JSRuntime(Path("unused.js"))
    seen = []
    runtime._progress_cbs[1] = lambda *args: seen.append(args)
    runtime._dispatch(
        {
            "type": "progress",
            "callId": 1,
            "value": 0,
            "bytesReceived": 262144,
            "bytesTotal": 0,
        }
    )
    assert seen == [(0.0, 262144, 0)]


def test_async_engine_progress_reaches_the_queue_before_completion(monkeypatch):
    from mornye.core.progress import DownloadManager, ProgressCallback, ProgressManager

    monkeypatch.setenv("MORNYE_PROGRESS_BARS", "0")

    async def scenario():
        manager = DownloadManager()
        await manager.reset()
        await manager.add_to_queue("t1", "Song", "Artist", "Album", "t1")
        await manager.start_download("t1")
        provider = object.__new__(JSExtensionProvider)
        provider.set_progress_callback(
            ProgressCallback(item_id="t1", track_name="Song")
        )
        await asyncio.to_thread(provider._progress_cb, 262144, 1048576)
        for _ in range(100):
            stats = await manager.get_stats()
            if stats["queue"][0]["progress"] > 0:
                break
            await asyncio.sleep(0.01)
        await ProgressManager.clear_all()
        await manager.reset()
        return stats["queue"][0]

    row = asyncio.run(scenario())
    assert row["status"] == "downloading"
    assert row["progress"] == pytest.approx(0.25)
    assert row["total_size"] == pytest.approx(1.0)
