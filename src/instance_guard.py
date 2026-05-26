"""Garante que apenas uma instância do EpicPen seja executada por sessão."""
import socket

_sock: socket.socket | None = None


def acquire() -> bool:
    """Tenta obter o bloqueio de instância única.

    Usa um socket de domínio Unix com nome abstrato (Linux).
    O SO libera o socket automaticamente quando o processo termina,
    portanto nenhuma limpeza manual é necessária.
    """
    global _sock
    _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        _sock.bind("\0epicpen-linux-instance")
        return True
    except OSError:
        _sock.close()
        _sock = None
        return False
