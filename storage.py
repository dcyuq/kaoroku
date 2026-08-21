import json
import os
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent

_data_dir = BOT_ROOT / "data"


def configure(path):
    global _data_dir
    _data_dir = Path(path).resolve()


def data_dir():
    return _data_dir


class Store:
    def __init__(self, filename, default=dict):
        self.name = filename
        self._default = default

    @property
    def path(self):
        return _data_dir / self.name

    @property
    def backup(self):
        return _data_dir / (self.name + ".bak")

    @property
    def legacy(self):
        return BOT_ROOT / self.name

    def load(self):
        for candidate in (self.path, self.backup, self.legacy):
            if not candidate.exists():
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if candidate != self.path:
                    print(f"[storage] recovered {self.name} from {candidate.name}")
                    self.save(data)
                return data
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[storage] {candidate.name} unreadable: {exc}")

        return self._default()

    def save(self, data):
        try:
            _data_dir.mkdir(parents=True, exist_ok=True)
            tmp = _data_dir / (self.name + ".tmp")

            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            if self.path.exists():
                os.replace(self.path, self.backup)

            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            print(f"[storage] failed to write {self.name}: {exc}")
            return False


class IntKeyStore(Store):
    def load(self):
        raw = super().load()
        if not isinstance(raw, dict):
            return self._default()
        try:
            return {int(k): v for k, v in raw.items()}
        except (ValueError, TypeError):
            print(f"[storage] {self.name} has non integer keys, discarding")
            return self._default()

    def save(self, data):
        return super().save({str(k): v for k, v in data.items()})