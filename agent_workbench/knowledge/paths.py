from pathlib import Path
import stat


def managed_file(path: str | Path, root: str | Path) -> Path:
    target, directory = Path(path), Path(root)
    if target.absolute().parent != directory.absolute():
        raise ValueError('Knowledge path is outside its managed directory')
    for candidate in (directory, target):
        try:
            attributes = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Knowledge links and reparse points are not supported')
    if target.resolve().parent != directory.resolve():
        raise ValueError('Knowledge path escapes its managed directory')
    return target
