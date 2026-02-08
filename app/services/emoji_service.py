from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.db.models import MediaKind


@dataclass(slots=True)
class EmojiSuggestion:
    top3: list[str]
    auto_pick: str
    confidence: float


class EmojiService:
    def __init__(self, catalog_path: Path, confidence_threshold: float = 0.2) -> None:
        self.catalog_path = catalog_path
        self.confidence_threshold = confidence_threshold
        self.catalog = self._load_catalog()
        self._model = None
        self._processor = None
        self._text_embeddings = None

    def initialize(self) -> None:
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor

            self._model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
            self._processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
            self._model.eval()

            descriptions = [", ".join(item["descriptions"]) for item in self.catalog]
            inputs = self._processor(text=descriptions, return_tensors="pt", padding=True, truncation=True)
            with torch.no_grad():
                emb = self._model.get_text_features(**inputs)
            emb = emb / emb.norm(p=2, dim=-1, keepdim=True)
            self._text_embeddings = emb
        except Exception:
            self._model = None
            self._processor = None
            self._text_embeddings = None

    def suggest(self, preview_path: Path, media_kind: MediaKind, video_path: Path | None = None) -> EmojiSuggestion:
        fallback = "🎬" if media_kind == MediaKind.VIDEO else "🖼️"

        if self._model is None or self._processor is None or self._text_embeddings is None:
            defaults = [fallback, "✨", "🔥"]
            return EmojiSuggestion(top3=defaults, auto_pick=fallback, confidence=0.0)

        try:
            vectors = self._image_vectors(preview_path=preview_path, media_kind=media_kind, video_path=video_path)
            if not vectors:
                defaults = [fallback, "✨", "🔥"]
                return EmojiSuggestion(top3=defaults, auto_pick=fallback, confidence=0.0)

            avg_vector = np.mean(vectors, axis=0)
            avg_vector = avg_vector / np.linalg.norm(avg_vector)

            import torch

            image_tensor = torch.tensor(avg_vector).unsqueeze(0)
            sims = (image_tensor @ self._text_embeddings.T).squeeze(0)
            top_values, top_indices = torch.topk(sims, k=min(3, len(self.catalog)))

            top3 = [self.catalog[int(i)]["emoji"] for i in top_indices]
            confidence = float(top_values[0]) if len(top_values) else 0.0
            auto_pick = top3[0] if top3 else fallback
            if confidence < self.confidence_threshold:
                auto_pick = fallback
            if len(top3) < 3:
                top3.extend([fallback, "✨", "🔥"])
                top3 = top3[:3]
            return EmojiSuggestion(top3=top3, auto_pick=auto_pick, confidence=confidence)
        except Exception:
            defaults = [fallback, "✨", "🔥"]
            return EmojiSuggestion(top3=defaults, auto_pick=fallback, confidence=0.0)

    def _image_vectors(self, preview_path: Path, media_kind: MediaKind, video_path: Path | None) -> list[np.ndarray]:
        paths: list[Path] = []
        if media_kind == MediaKind.VIDEO and video_path and shutil.which("ffmpeg"):
            frame_dir = preview_path.parent / "emoji_frames"
            frame_dir.mkdir(parents=True, exist_ok=True)
            times = [0.5, 1.5, 2.5]
            for idx, sec in enumerate(times):
                frame_path = frame_dir / f"frame_{idx}.png"
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(sec),
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    str(frame_path),
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0 and frame_path.exists():
                    paths.append(frame_path)

        if not paths:
            paths = [preview_path]

        vectors: list[np.ndarray] = []
        import torch

        for path in paths:
            with Image.open(path) as img:
                image = img.convert("RGB")
                inputs = self._processor(images=image, return_tensors="pt")
                with torch.no_grad():
                    feats = self._model.get_image_features(**inputs)
                feats = feats / feats.norm(p=2, dim=-1, keepdim=True)
                vector = feats.squeeze(0).cpu().numpy()
                vectors.append(vector)
        return vectors

    def _load_catalog(self) -> list[dict[str, list[str] | str]]:
        data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("emoji_catalog.json should be a list")
        cleaned = []
        for item in data:
            emoji = str(item.get("emoji", "")).strip()
            descriptions = item.get("descriptions", [])
            if not emoji or not isinstance(descriptions, list) or not descriptions:
                continue
            cleaned.append({"emoji": emoji, "descriptions": [str(x) for x in descriptions]})
        if not cleaned:
            raise ValueError("emoji catalog is empty")
        return cleaned
