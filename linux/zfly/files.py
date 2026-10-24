"""Bounded imports and private, atomic exports through Gio.File."""
from gi.repository import Gio, GLib

from .controller import ControllerError
from .i18n import tr


def read_file(target, limit, title="文件", cancellable=None):
    try:
        stream = target.read(cancellable)
        try:
            chunks, length = [], 0
            while True:
                chunk = stream.read_bytes(min(65536, limit + 1 - length), cancellable).get_data()
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
                length += len(chunk)
                if length > limit:
                    raise ControllerError(tr("{title}不能超过 {limit} MiB。", title=tr(title), limit=limit // (1024 * 1024)))
        finally:
            stream.close(None)
    except GLib.Error as error:
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
            raise ControllerError(tr("读取已取消。")) from None
        raise ControllerError(tr("无法读取所选文件，请检查文件权限和连接。")) from None


def write_file(target, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        target.replace_contents(data, None, False,
                                Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, None)
    except GLib.Error:
        raise ControllerError(tr("导出失败，请检查目标文件权限和连接。")) from None
