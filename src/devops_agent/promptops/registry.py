"""Rejestr wariantów promptów z obsługą A/B testów.

Przepływ:
1. Zarejestruj warianty (register_variant / load_from_dir).
2. Wywołaj get_prompt(name, trace_id) – wariant wybierany jest deterministycznie
   na podstawie hash(trace_id) tak, żeby ten sam trace zawsze dostał ten sam wariant.
3. Wyniki są zapisywane w pliku JSON, żeby można było skorelować trace z wariantem.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from devops_agent.promptops.models import ABAssignment, PromptVariant

logger = logging.getLogger(__name__)

_DEFAULT_VARIANTS_DIR = Path(__file__).parent / "variants"
_DEFAULT_ASSIGNMENTS_FILE = Path(__file__).parent.parent.parent.parent / "data" / "promptops" / "ab_assignments.jsonl"


class PromptRegistry:
    """Przechowuje warianty promptów i zarządza przypisaniami A/B."""

    def __init__(
        self,
        variants_dir: Path | None = None,
        assignments_file: Path | None = None,
    ) -> None:
        self._variants: dict[str, list[PromptVariant]] = {}
        # name -> list of variants sorted by variant_id
        self._lock = threading.Lock()
        self._variants_dir = variants_dir or _DEFAULT_VARIANTS_DIR
        self._assignments_file = assignments_file or _DEFAULT_ASSIGNMENTS_FILE

        self._variants_dir.mkdir(parents=True, exist_ok=True)
        self._assignments_file.parent.mkdir(parents=True, exist_ok=True)

        self._load_from_dir(self._variants_dir)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_variant(self, variant: PromptVariant) -> None:
        """Zarejestruj wariant. Nadpisuje istniejący jeśli variant_id się powtarza."""
        with self._lock:
            variants = self._variants.setdefault(variant.name, [])
            # Replace if already exists
            for i, v in enumerate(variants):
                if v.variant_id == variant.variant_id:
                    variants[i] = variant
                    logger.info("Updated prompt variant %s", variant.variant_id)
                    return
            variants.append(variant)
            logger.info("Registered prompt variant %s (weight=%.2f)", variant.variant_id, variant.weight)

    def _load_from_dir(self, directory: Path) -> None:
        """Wczytuje warianty z plików *.md w podkatalogach.

        Oczekiwana struktura:
            variants/<name>/<variant_id>.md

        Opcjonalny plik meta.json obok *.md może zawierać dodatkowe pola
        (weight, active, description).
        """
        if not directory.exists():
            return
        for name_dir in sorted(directory.iterdir()):
            if not name_dir.is_dir():
                continue
            name = name_dir.name
            for md_file in sorted(name_dir.glob("*.md")):
                variant_id = md_file.stem
                content = md_file.read_text(encoding="utf-8").strip()
                meta: dict[str, Any] = {}
                meta_file = md_file.with_suffix(".json")
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text())
                    except json.JSONDecodeError:
                        logger.warning("Nieprawidłowy meta.json: %s", meta_file)
                variant = PromptVariant(
                    name=name,
                    variant_id=variant_id,
                    content=content,
                    weight=float(meta.get("weight", 1.0)),
                    active=bool(meta.get("active", True)),
                    description=str(meta.get("description", "")),
                )
                self.register_variant(variant)

    def save_variant(self, variant: PromptVariant) -> None:
        """Zapisuje wariant na dysk (do katalogu variants)."""
        out_dir = self._variants_dir / variant.name
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / f"{variant.variant_id}.md"
        md_path.write_text(variant.content, encoding="utf-8")
        meta = {
            "weight": variant.weight,
            "active": variant.active,
            "description": variant.description,
        }
        (out_dir / f"{variant.variant_id}.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        self.register_variant(variant)
        logger.info("Saved variant %s to %s", variant.variant_id, md_path)

    # ------------------------------------------------------------------
    # A/B selection
    # ------------------------------------------------------------------

    def get_prompt(
        self, name: str, trace_id: str | None = None
    ) -> tuple[str, str]:
        """Zwraca (treść prompta, variant_id).

        Wybór wariantu jest deterministyczny względem trace_id – ten sam
        trace zawsze dostanie ten sam wariant. Przy braku trace_id wybierany
        jest wariant o najwyższej wadze.
        """
        with self._lock:
            variants = [v for v in self._variants.get(name, []) if v.active]

        if not variants:
            return "", f"{name}_default"

        if len(variants) == 1:
            v = variants[0]
            return v.content, v.variant_id

        # Ważone losowanie deterministyczne na podstawie trace_id
        chosen = self._weighted_select(variants, trace_id)
        if trace_id:
            self._record_assignment(trace_id, chosen.variant_id)
        return chosen.content, chosen.variant_id

    def _weighted_select(
        self, variants: list[PromptVariant], trace_id: str | None
    ) -> PromptVariant:
        """Deterministyczny wybór wariantu oparty o hash trace_id."""
        total = sum(v.weight for v in variants)
        if trace_id:
            h = int(hashlib.md5(trace_id.encode(), usedforsecurity=False).hexdigest(), 16)
            bucket = (h % 10000) / 10000.0 * total
        else:
            # Brak trace_id – wybierz najcięższy
            return max(variants, key=lambda v: v.weight)

        cumulative = 0.0
        for v in variants:
            cumulative += v.weight
            if bucket < cumulative:
                return v
        return variants[-1]

    def _record_assignment(self, trace_id: str, variant_id: str) -> None:
        assignment = ABAssignment(trace_id=trace_id, variant_id=variant_id)
        try:
            with open(self._assignments_file, "a", encoding="utf-8") as f:
                f.write(assignment.model_dump_json() + "\n")
        except OSError as exc:
            logger.warning("Nie można zapisać przypisania A/B: %s", exc)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def list_variants(self, name: str | None = None) -> list[PromptVariant]:
        with self._lock:
            if name:
                return list(self._variants.get(name, []))
            return [v for variants in self._variants.values() for v in variants]

    def get_variant(self, variant_id: str) -> PromptVariant | None:
        for v in self.list_variants():
            if v.variant_id == variant_id:
                return v
        return None

    def list_names(self) -> list[str]:
        with self._lock:
            return list(self._variants.keys())

    def load_ab_assignments(self) -> list[ABAssignment]:
        """Wczytuje historię przypisań A/B z pliku JSONL."""
        if not self._assignments_file.exists():
            return []
        assignments = []
        with open(self._assignments_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        assignments.append(ABAssignment.model_validate_json(line))
                    except Exception:
                        pass
        return assignments

    def ab_stats(self) -> dict[str, dict[str, int]]:
        """Liczy ile razy każdy wariant był przypisany.

        Returns: {name -> {variant_id -> count}}
        """
        assignments = self.load_ab_assignments()
        stats: dict[str, dict[str, int]] = {}
        for a in assignments:
            # variant_id format: "<name>_<version>"
            parts = a.variant_id.rsplit("_", 1)
            name = parts[0] if len(parts) == 2 else a.variant_id
            stats.setdefault(name, {})
            stats[name][a.variant_id] = stats[name].get(a.variant_id, 0) + 1
        return stats


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry: PromptRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> PromptRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = PromptRegistry()
    return _registry
