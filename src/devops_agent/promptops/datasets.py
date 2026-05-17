"""Zarządzanie datasetami do ewaluacji promptów.

Datasety przechowywane są w:
    data/promptops/datasets/<event_kind>.json

Każdy plik to serializowany EvalDataset (Pydantic JSON).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from devops_agent.promptops.models import EvalCase, EvalDataset

logger = logging.getLogger(__name__)

_DEFAULT_DATASETS_DIR = (
    Path(__file__).parent.parent.parent.parent / "data" / "promptops" / "datasets"
)


class DatasetManager:
    """CRUD dla datasetów ewaluacyjnych."""

    def __init__(self, datasets_dir: Path | None = None) -> None:
        self._dir = datasets_dir or _DEFAULT_DATASETS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _path(self, event_kind: str) -> Path:
        return self._dir / f"{event_kind}.json"

    def load(self, event_kind: str) -> EvalDataset:
        """Wczytuje dataset z dysku. Jeśli nie istnieje, zwraca pusty."""
        path = self._path(event_kind)
        if not path.exists():
            return EvalDataset(event_kind=event_kind)
        try:
            raw = path.read_text(encoding="utf-8")
            return EvalDataset.model_validate_json(raw)
        except Exception as exc:
            logger.error("Błąd wczytywania datasetu %s: %s", event_kind, exc)
            return EvalDataset(event_kind=event_kind)

    def save(self, dataset: EvalDataset) -> None:
        """Zapisuje dataset na dysk (atomowo przez plik tymczasowy)."""
        path = self._path(dataset.event_kind)
        dataset.updated_at = datetime.now(timezone.utc)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.error("Błąd zapisu datasetu %s: %s", dataset.event_kind, exc)
            raise

    # ------------------------------------------------------------------
    # Case management
    # ------------------------------------------------------------------

    def add_case(self, case: EvalCase) -> EvalCase:
        """Dodaje przypadek do datasetu. Zwraca case z nadanym case_id."""
        with self._lock:
            dataset = self.load(case.event_kind)
            # Unikaj duplikatów po case_id
            if any(c.case_id == case.case_id for c in dataset.cases):
                raise ValueError(f"case_id '{case.case_id}' już istnieje w {case.event_kind}")
            dataset.cases.append(case)
            self.save(dataset)
        logger.info("Dodano case %s do datasetu %s", case.case_id, case.event_kind)
        return case

    def remove_case(self, event_kind: str, case_id: str) -> bool:
        """Usuwa przypadek. Zwraca True jeśli usunięto."""
        with self._lock:
            dataset = self.load(event_kind)
            before = len(dataset.cases)
            dataset.cases = [c for c in dataset.cases if c.case_id != case_id]
            if len(dataset.cases) == before:
                return False
            self.save(dataset)
        return True

    def get_case(self, event_kind: str, case_id: str) -> EvalCase | None:
        dataset = self.load(event_kind)
        for c in dataset.cases:
            if c.case_id == case_id:
                return c
        return None

    def list_datasets(self) -> list[str]:
        """Zwraca listę dostępnych event_kind (na podstawie plików JSON)."""
        return [p.stem for p in sorted(self._dir.glob("*.json"))]

    def import_from_file(self, path: Path) -> EvalDataset:
        """Importuje dataset z zewnętrznego pliku JSON (format EvalDataset)."""
        raw = Path(path).read_text(encoding="utf-8")
        dataset = EvalDataset.model_validate_json(raw)
        with self._lock:
            existing = self.load(dataset.event_kind)
            existing_ids = {c.case_id for c in existing.cases}
            added = 0
            for case in dataset.cases:
                if case.case_id not in existing_ids:
                    existing.cases.append(case)
                    added += 1
            self.save(existing)
        logger.info(
            "Import %s: dodano %d/%d przypadków", dataset.event_kind, added, len(dataset.cases)
        )
        return existing

    def export_to_file(self, event_kind: str, path: Path) -> None:
        """Eksportuje dataset do zewnętrznego pliku."""
        dataset = self.load(event_kind)
        Path(path).write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Eksport %s -> %s (%d cases)", event_kind, path, len(dataset.cases))

    def stats(self) -> dict[str, int]:
        """Zwraca {event_kind: liczba przypadków}."""
        return {ek: len(self.load(ek).cases) for ek in self.list_datasets()}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_mgr: DatasetManager | None = None
_mgr_lock = threading.Lock()


def get_dataset_manager() -> DatasetManager:
    global _mgr
    with _mgr_lock:
        if _mgr is None:
            _mgr = DatasetManager()
    return _mgr
