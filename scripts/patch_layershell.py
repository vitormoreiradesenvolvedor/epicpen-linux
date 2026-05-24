#!/usr/bin/env python3
"""
Cria uma cópia local de libLayerShellQtInterface.so compatível com pip PyQt6.

O pip PyQt6 compila Qt sem symbol versioning (versão "Qt_6_PRIVATE_API" em vez
de "Qt_6.10_PRIVATE_API") enquanto o layer-shell-qt do sistema foi compilado
contra Qt 6.10.1 do sistema.  Dois patches ELF são necessários:

  1. Verneed WEAK — .gnu.version_r: Qt_6.10_PRIVATE_API → vna_flags |= 0x02
     Impede falha de carga quando o símbolo não é encontrado com essa versão.

  2. Symver GLOBAL — .gnu.version: índices de Qt_6.10_PRIVATE_API → 1 (global)
     Faz o linker resolver cada símbolo pelo nome sem exigir versão exata,
     encontrando a versão Qt_6_PRIVATE_API (ou não-versionada) que PyQt6 tem.

Uso:
    python3 scripts/patch_layershell.py          # gera lib/libLayerShellQtInterface.so.6
    python3 scripts/patch_layershell.py --verify  # testa carregamento após patch
"""
import struct, os, sys

SRC = "/usr/lib64/libLayerShellQtInterface.so.6.5.3"
DST = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                   "lib", "libLayerShellQtInterface.so.6")

VER_FLG_WEAK   = 0x02
VER_NDX_GLOBAL = 1           # índice "global / unversioned" em .gnu.version
VERSYM_HIDDEN  = 0x8000      # bit de símbolo oculto em .gnu.version
TARGET_VERSION = "Qt_6.10_PRIVATE_API"


def _get_str(blob: bytes, off: int) -> str:
    end = blob.index(b"\x00", off)
    return blob[off:end].decode("ascii", errors="replace")


def patch(src: str, dst: str) -> dict:
    with open(src, "rb") as f:
        data = bytearray(f.read())

    assert data[:4] == b"\x7fELF", "não é ELF"
    assert data[4] == 2 and data[5] == 1, "apenas ELF64 LE suportado"

    e_shoff     = struct.unpack_from("<Q", data, 40)[0]
    e_shentsize = struct.unpack_from("<H", data, 58)[0]
    e_shnum     = struct.unpack_from("<H", data, 60)[0]
    e_shstrndx  = struct.unpack_from("<H", data, 62)[0]

    def section(idx):
        off = e_shoff + idx * e_shentsize
        name_off          = struct.unpack_from("<I",  data, off)[0]
        sh_offset, sh_size = struct.unpack_from("<QQ", data, off + 24)
        return name_off, sh_offset, sh_size

    _, shstr_off, shstr_sz = section(e_shstrndx)
    shstr = bytes(data[shstr_off: shstr_off + shstr_sz])

    sections = {}
    for i in range(e_shnum):
        noff, soff, ssz = section(i)
        name = _get_str(shstr, noff)
        sections[name] = (soff, ssz)

    assert ".gnu.version_r" in sections, ".gnu.version_r não encontrada"
    assert ".dynstr"        in sections, ".dynstr não encontrada"
    assert ".gnu.version"   in sections, ".gnu.version não encontrada"

    ver_r_off,  ver_r_sz  = sections[".gnu.version_r"]
    dynstr_off, dynstr_sz = sections[".dynstr"]
    ver_off,    ver_sz    = sections[".gnu.version"]
    dynstr = bytes(data[dynstr_off: dynstr_off + dynstr_sz])

    # ── Passo 1: Verneed WEAK + coletar vna_other (version indices) ─────────
    target_ver_indices: set[int] = set()
    weak_count = 0
    vn_off = 0
    while vn_off < ver_r_sz:
        base    = ver_r_off + vn_off
        vn_version, vn_cnt, vn_file, vn_aux, vn_next = struct.unpack_from("<HHIII", data, base)
        file_name = _get_str(dynstr, vn_file)

        aux_off = vn_off + vn_aux
        for _ in range(vn_cnt):
            aux_base = ver_r_off + aux_off
            vna_hash, vna_flags, vna_other, vna_name, vna_next = struct.unpack_from("<IHHII", data, aux_base)
            ver_name = _get_str(dynstr, vna_name)

            if ver_name == TARGET_VERSION:
                new_flags = vna_flags | VER_FLG_WEAK
                struct.pack_into("<H", data, aux_base + 4, new_flags)
                target_ver_indices.add(vna_other)
                print(f"  [verneed WEAK]  {file_name}::{ver_name}  "
                      f"flags 0x{vna_flags:02x}→0x{new_flags:02x}  idx={vna_other}")
                weak_count += 1

            if vna_next == 0:
                break
            aux_off += vna_next

        if vn_next == 0:
            break
        vn_off += vn_next

    # ── Passo 2: .gnu.version → global (1) para refs Qt_6.10_PRIVATE_API ───
    symver_count = 0
    n_entries = ver_sz // 2
    for i in range(n_entries):
        raw = struct.unpack_from("<H", data, ver_off + i * 2)[0]
        actual_idx = raw & ~VERSYM_HIDDEN
        if actual_idx in target_ver_indices:
            new_raw = (raw & VERSYM_HIDDEN) | VER_NDX_GLOBAL
            struct.pack_into("<H", data, ver_off + i * 2, new_raw)
            symver_count += 1

    print(f"  [gnu.version]   {symver_count} símbolo(s) → global (unversioned)")

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(data)
    os.chmod(dst, 0o755)
    return {"weak": weak_count, "symver": symver_count}


def verify(dst: str) -> bool:
    import ctypes
    try:
        lib = ctypes.CDLL(dst)
        fn  = lib["_ZN12LayerShellQt6Window3getEP7QWindow"]
        print(f"  OK: carregada, Window::get() acessível → {fn}")
        return True
    except Exception as e:
        print(f"  ERRO: {e}")
        return False


if __name__ == "__main__":
    do_verify = "--verify" in sys.argv
    print(f"Fonte  : {SRC}")
    print(f"Destino: {DST}")
    stats = patch(SRC, DST)
    print(f"Patches: {stats}")
    if stats["weak"] == 0:
        print("AVISO: nenhum verneed patchado — versão já compatível ou seção ausente")
    if do_verify:
        print("Verificando com ctypes...")
        verify(DST)
