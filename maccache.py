#!/usr/bin/env python3
"""
maccache — audita e limpa cache no macOS.

Uso:
    python3 maccache.py scan
    python3 maccache.py scan --json
    python3 maccache.py clean --dry-run
    python3 maccache.py clean
    python3 maccache.py clean --category tudo --yes

Categorias:
    seguro     cache de verdade; o app recria sozinho
    cuidado    apagável, mas pode custar tempo/reconfiguração
    protegido  NUNCA é apagado por esta ferramenta (dados pessoais)
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SEGURO = "seguro"
CUIDADO = "cuidado"
PROTEGIDO = "protegido"

CATEGORIAS = (SEGURO, CUIDADO, PROTEGIDO)

# Nomes de subpastas que são cache dentro de Application Support
CACHE_DIR_NAMES = {
    "Cache",
    "Code Cache",
    "CachedData",
    "CachedExtensionVSIXs",
    "GPUCache",
    "DawnCache",
    "DawnWebGPUCache",
    "GraphiteDawnCache",
    "GrShaderCache",
    "ShaderCache",
}

# Conteúdo de navegador que parece cache, mas pode guardar dados offline
BROWSER_CAUTION_NAMES = {"Service Worker", "IndexedDB"}


def _home() -> Path:
    """Home do usuário real, mesmo quando executado via sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.geteuid() == 0:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


HOME = _home()
LIB = HOME / "Library"
APP_SUPPORT = LIB / "Application Support"


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------


@dataclass
class Item:
    label: str
    path: Path
    category: str
    note: str = ""
    size: int = 0

    @property
    def human(self) -> str:
        return human_size(self.size)


@dataclass
class Catalog:
    items: list[Item] = field(default_factory=list)
    _seen: set[Path] = field(default_factory=set)

    def add(self, path: Path | str, category: str, label: str | None = None,
            note: str = "") -> None:
        p = Path(os.path.expanduser(str(path)))
        try:
            if not (p.exists() or p.is_symlink()):
                return
        except OSError:
            return
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append(Item(label or p.name, p, category, note))

    def by_category(self, category: str) -> list[Item]:
        return sorted(
            (i for i in self.items if i.category == category and i.size > 0),
            key=lambda i: i.size,
            reverse=True,
        )


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def human_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def display_path(p: Path) -> str:
    try:
        rel = p.relative_to(HOME)
        return "~/" + str(rel)
    except ValueError:
        return str(p)


def path_size(path: Path) -> int:
    """Soma o espaço em disco (blocos) de um arquivo ou árvore."""
    total = 0
    try:
        if path.is_symlink() or path.is_file():
            st = path.stat()
            return getattr(st, "st_blocks", 0) * 512 or st.st_size
        stack = [str(path)]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            else:
                                st = entry.stat(follow_symlinks=False)
                                total += getattr(st, "st_blocks", 0) * 512 or st.st_size
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return total


def compute_sizes(items: list[Item], workers: int = 8) -> None:
    if not items:
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        sizes = list(pool.map(lambda i: path_size(i.path), items))
    for item, size in zip(items, sizes):
        item.size = size


def is_writable(path: Path) -> bool:
    """True se o usuário atual consegue apagar o item."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK)


def disk_summary() -> tuple[int, int, int]:
    usage = shutil.disk_usage(str(HOME))
    return usage.total, usage.used, usage.free


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------


def build_catalog() -> Catalog:
    cat = Catalog()

    # 1) Caches do usuário (~/Library/Caches/*) — cache puro por definição
    caches_dir = LIB / "Caches"
    if caches_dir.is_dir():
        try:
            children = sorted(caches_dir.iterdir())
        except OSError:
            children = []
        for child in children:
            note = ""
            if child.name == "Homebrew":
                note = "downloads do brew (recriado na próxima instalação)"
            cat.add(child, SEGURO, f"Caches/{child.name}", note)

    # 2) Caches dentro de Application Support (inclusive perfis de navegador)
    def scan_tree(base: Path, app_name: str, depth: int, max_depth: int = 3) -> None:
        try:
            subs = sorted(base.iterdir())
        except OSError:
            return
        for sub in subs:
            try:
                is_dir = sub.is_dir() and not sub.is_symlink()
            except OSError:
                continue
            if not is_dir:
                continue
            rel = f"{app_name}/{sub.relative_to(APP_SUPPORT)}"
            if sub.name in CACHE_DIR_NAMES:
                cat.add(sub, SEGURO, rel, f"cache do {app_name}")
                continue  # não precisa descer dentro de um cache
            if sub.name in BROWSER_CAUTION_NAMES:
                cat.add(sub, CUIDADO, rel, "pode conter dados offline do navegador")
                continue
            if sub.name == "workspaceStorage":
                cat.add(sub, CUIDADO, rel,
                        "estado/layout dos workspaces (o editor recria)")
                continue
            if depth < max_depth:
                scan_tree(sub, app_name, depth + 1, max_depth)

    if APP_SUPPORT.is_dir():
        try:
            apps = sorted(APP_SUPPORT.iterdir())
        except OSError:
            apps = []
        for app in apps:
            if app.is_dir() and not app.is_symlink() and app.name != "Caches":
                scan_tree(app, app.name, depth=1)

    # 3) Caches específicos conhecidos
    cat.add(APP_SUPPORT / "stremio-server" / "stremio-cache", SEGURO,
            "stremio-cache", "cache de vídeo do Stremio")
    cat.add(APP_SUPPORT / "MobileSync" / "Backup", CUIDADO,
            "Backup do iPhone/iPad", "backup local de dispositivo iOS")
    cat.add(LIB / "Logs", SEGURO, "Logs do usuário", "logs antigos")

    # 4) Simuladores e ferramentas de desenvolvimento
    cat.add(LIB / "Developer" / "CoreSimulator" / "Caches", SEGURO,
            "CoreSimulator/Caches", "cache do simulador iOS")
    cat.add(LIB / "Developer" / "CoreSimulator" / "Devices", CUIDADO,
            "CoreSimulator/Devices", "apps e dados dos simuladores iOS")
    cat.add(LIB / "Developer" / "Xcode" / "DerivedData", SEGURO,
            "Xcode/DerivedData", "artefatos de build (recompiláveis)")
    cat.add(LIB / "Developer" / "Xcode" / "DocumentationCache", SEGURO,
            "Xcode/DocumentationCache", "documentação baixada")
    cat.add(HOME / ".npm" / "_cacache", SEGURO, "npm cache", "pacotes do npm")
    cat.add(LIB / "pnpm" / "store", SEGURO, "pnpm store", "pacotes do pnpm")
    cat.add(HOME / ".cache", SEGURO, "~/.cache", "cache genérico de ferramentas")
    cat.add(HOME / ".gradle" / "caches", SEGURO, "Gradle caches", "dependências re-baixáveis")
    cat.add(HOME / ".m2" / "repository", CUIDADO, "Maven repository",
            "dependências Java (re-baixáveis, mas grandes)")

    # 5) Sistema (precisa de sudo)
    cat.add(Path("/Library/Caches"), SEGURO, "/Library/Caches", "cache de apps do sistema")
    cat.add(Path("/Library/Updates"), SEGURO, "/Library/Updates",
            "restos de atualizações do macOS")

    # 6) Docker e Lixeira (não são cache)
    cat.add(LIB / "Containers" / "com.docker.docker", CUIDADO, "Docker",
            "imagens e volumes (use docker system prune)")
    cat.add(HOME / ".Trash", CUIDADO, "Lixeira", "confira o conteúdo antes")

    # 7) Protegidos — exibidos, jamais apagados
    protected = [
        (HOME / "Desktop", "arquivos pessoais"),
        (HOME / "Documents", "arquivos pessoais"),
        (HOME / "Downloads", "arquivos baixados"),
        (HOME / "Pictures", "fotos"),
        (HOME / "Movies", "vídeos"),
        (HOME / "Music", "música"),
        (LIB / "Keychains", "senhas e certificados"),
        (LIB / "Mail", "e-mails"),
        (LIB / "Messages", "mensagens"),
        (Path("/private/var/vm"), "memória swap do sistema"),
        (Path("/Applications"), "aplicativos instalados"),
    ]
    for path, note in protected:
        cat.add(path, PROTEGIDO, note=note)

    compute_sizes(cat.items)
    return cat


# ---------------------------------------------------------------------------
# Exibição
# ---------------------------------------------------------------------------


def color(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def category_color(category: str) -> str:
    return {SEGURO: "32", CUIDADO: "33", PROTEGIDO: "34"}.get(category, "0")


def print_header() -> None:
    total, used, free = disk_summary()
    print("maccache — auditoria de cache do macOS")
    print(f"Disco: {human_size(total)} total · {human_size(used)} usados · "
          f"{human_size(free)} livres\n")


def print_category(cat: Catalog, category: str, use_color: bool, min_size: int) -> int:
    items = [i for i in cat.by_category(category) if i.size >= min_size]
    total = sum(i.size for i in items)
    title = {
        SEGURO: "SEGURO — pode apagar",
        CUIDADO: "CUIDADO — apague só se souber",
        PROTEGIDO: "PROTEGIDO — nunca removemos",
    }[category]
    head = f"{title} ({len(items)} itens, {human_size(total)})"
    print(color(head, category_color(category), use_color))
    if not items:
        print("  (nada acima do tamanho mínimo)\n")
        return 0
    for item in items:
        print(f"  {item.human:>9}  {display_path(item.path)}")
        if item.note:
            print(f"             {color(item.note, '2', use_color)}")
    print()
    return total


def cmd_scan(args: argparse.Namespace) -> int:
    cat = build_catalog()
    min_size = int(args.min_size * 1024 * 1024)

    if args.json:
        data = [
            {
                "label": i.label,
                "path": str(i.path),
                "category": i.category,
                "size": i.size,
                "size_human": i.human,
                "note": i.note,
            }
            for i in cat.items
            if i.size >= min_size
        ]
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    use_color = sys.stdout.isatty()
    print_header()
    totals = {}
    for category in CATEGORIAS:
        totals[category] = print_category(cat, category, use_color, min_size)

    removivel = totals[SEGURO] + totals[CUIDADO]
    print(color(f"Resumo: seguro {human_size(totals[SEGURO])} · "
                f"cuidado {human_size(totals[CUIDADO])} · "
                f"removível {human_size(removivel)}", "1", use_color))
    if args.min_size > 0:
        print(color(f"(itens menores que {args.min_size:g} MB foram ocultados; "
                    f"use --min-size 0 para ver tudo)", "2", use_color))
    return 0


# ---------------------------------------------------------------------------
# Limpeza
# ---------------------------------------------------------------------------


def parse_selection(text: str, count: int) -> list[int]:
    """Aceita '1,3,5-7', 'todos' ou '' (nada)."""
    text = text.strip().lower()
    if not text:
        return []
    if text in {"todos", "all", "*"}:
        return list(range(1, count + 1))
    selected: set[int] = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            selected.update(range(start, end + 1))
        else:
            try:
                selected.add(int(part))
            except ValueError:
                continue
    return sorted(n for n in selected if 1 <= n <= count)


def remove_path(path: Path) -> None:
    """Remove arquivo, symlink ou árvore, de forma rápida e segura."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        subprocess.run(["rm", "-rf", "--", str(path)], check=True)
    else:
        raise FileNotFoundError(str(path))


def guard_path(path: Path) -> str | None:
    """Retorna motivo para recusar, ou None se pode apagar."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if resolved == Path("/") or resolved == HOME:
        return "recusado: caminho raiz"
    if len(resolved.parts) <= 2:
        return "recusado: caminho muito raso"
    if resolved == LIB or resolved == APP_SUPPORT:
        return "recusado: pasta de dados do usuário"
    return None


def cmd_clean(args: argparse.Namespace) -> int:
    cat = build_catalog()
    min_size = int(args.min_size * 1024 * 1024)

    wanted = {SEGURO} if args.category == "seguro" else {SEGURO, CUIDADO}
    candidates = [
        i for i in cat.items
        if i.category in wanted and i.size >= min_size
    ]
    candidates.sort(key=lambda i: i.size, reverse=True)

    if not candidates:
        print("Nada para limpar com esses critérios.")
        return 0

    use_color = sys.stdout.isatty()
    print(color("Itens removíveis:", "1", use_color))
    for idx, item in enumerate(candidates, 1):
        tag = "seguro " if item.category == SEGURO else "cuidado"
        print(f"  [{idx:>2}] {item.human:>9}  {color(tag, category_color(item.category), use_color)}  "
              f"{display_path(item.path)}")
        if item.note:
            print(f"        {color(item.note, '2', use_color)}")
    print()

    if args.yes:
        selected = list(range(1, len(candidates) + 1))
    else:
        if not sys.stdin.isatty():
            print("Entrada não interativa: use --yes para confirmar.", file=sys.stderr)
            return 1
        try:
            raw = input("Quais remover? (ex: 1,3,5-7 | 'todos' | Enter cancela): ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            return 130
        selected = parse_selection(raw, len(candidates))
        if not selected:
            print("Cancelado.")
            return 0

    chosen = [candidates[n - 1] for n in selected]
    total = sum(i.size for i in chosen)

    if args.dry_run:
        print(color(f"[dry-run] Nada foi apagado. Seriam liberados {human_size(total)} "
                    f"de {len(chosen)} item(ns).", "33", use_color))
        return 0

    if not args.yes:
        try:
            confirm = input(
                f"Apagar {len(chosen)} item(ns) e liberar ~{human_size(total)}? "
                f"Digite 'remover' para confirmar: "
            )
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            return 130
        if confirm.strip().lower() != "remover":
            print("Cancelado.")
            return 0

    freed = 0
    failures = 0
    for item in chosen:
        reason = guard_path(item.path)
        if reason:
            print(f"  pulado  {display_path(item.path)}  ({reason})")
            failures += 1
            continue
        if not is_writable(item.path):
            print(f"  sem permissão  {display_path(item.path)}  "
                  f"(rode com sudo)")
            failures += 1
            continue
        try:
            remove_path(item.path)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"  falhou  {display_path(item.path)}  ({exc})")
            failures += 1
            continue
        freed += item.size
        print(f"  ok      {item.human:>9}  {display_path(item.path)}")

    print()
    print(color(f"Liberado: {human_size(freed)}", "32", use_color))
    if failures:
        print(color(f"{failures} item(ns) não puderam ser removidos.", "33", use_color))
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maccache",
        description="Audita e limpa cache no macOS com segurança.",
    )
    parser.add_argument("--version", action="version", version="maccache 1.0")

    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="mostra o que ocupa espaço e o que é cache")
    scan.add_argument("--json", action="store_true", help="saída em JSON")
    scan.add_argument("--min-size", type=float, default=1.0,
                      metavar="MB", help="tamanho mínimo para exibir (padrão: 1 MB)")
    scan.set_defaults(func=cmd_scan)

    clean = sub.add_parser("clean", help="remove itens de cache com confirmação")
    clean.add_argument("--category", choices=["seguro", "tudo"], default="seguro",
                       help="seguro (padrão) ou tudo (inclui 'cuidado')")
    clean.add_argument("--dry-run", action="store_true",
                       help="mostra o que seria apagado, sem apagar")
    clean.add_argument("--yes", action="store_true",
                       help="não pergunta (use com --dry-run primeiro)")
    clean.add_argument("--min-size", type=float, default=1.0,
                       metavar="MB", help="ignora itens menores que isso (padrão: 1 MB)")
    clean.set_defaults(func=cmd_clean)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
