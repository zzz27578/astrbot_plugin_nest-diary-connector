from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nest_diary_web.paths import NestPaths, normalize_date, atomic_write_text

try:
    from PIL import Image
except Exception:  # pragma: no cover - Pillow is optional at runtime.
    Image = None


class MediaService:
    def __init__(self, paths: NestPaths):
        self.paths = paths
        self.paths.ensure_all()

    def save_media(
        self,
        source_path: Path,
        date: str,
        original_name: str | None = None,
        note: str | None = None,
        storage_strategy: str = "copy",
    ) -> dict:
        source = Path(source_path)
        digest = self._sha256(source)
        suffix = source.suffix.lower()
        blob_path = self._blob_path(digest, suffix)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        strategy = "move" if storage_strategy in {"move", "cut"} else "copy"
        if not blob_path.exists():
            if strategy == "move":
                shutil.move(str(source), blob_path)
            else:
                shutil.copy2(source, blob_path)
        elif strategy == "move" and self._different_paths(source, blob_path):
            source.unlink(missing_ok=True)

        manifest_path = self._manifest_path(date)
        manifest = self._read_manifest(manifest_path, date)
        record = {
            "sha256": digest,
            "path": str(blob_path),
            "url": f"/media/blobs/{digest}",
            "original_name": original_name or source.name,
            "date": date,
            "note": (note or "").strip(),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "storage_strategy": strategy,
        }
        record.update(self._file_metadata(blob_path))
        existing = next((item for item in manifest["assets"] if item["sha256"] == digest), None)
        if existing:
            existing.update({key: value for key, value in record.items() if value not in ("", None)})
            record = existing
        else:
            manifest["assets"].append(record)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
        return record

    def _sha256(self, path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _blob_path(self, digest: str, suffix: str) -> Path:
        return self.paths.media_dir / "blobs" / "sha256" / digest[:2] / digest[2:4] / f"{digest}{suffix}"

    def _manifest_path(self, date: str) -> Path:
        date = normalize_date(date)
        year, month, _day = date.split("-")
        return self.paths.media_dir / "by-date" / year / month / date / "manifest.json"

    def _read_manifest(self, path: Path, date: str) -> dict:
        if not path.exists():
            return {"date": date, "assets": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def list_manifests(self) -> list[dict]:
        root = self.paths.media_dir / "by-date"
        if not root.exists():
            return []
        organization = self.load_organization()
        manifests = []
        for path in sorted(root.glob("*/*/*/manifest.json"), reverse=True):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                manifest["assets"] = [
                    self._with_output_metadata(asset, manifest.get("date", ""), organization)
                    for asset in manifest.get("assets", [])
                ]
                manifests.append(manifest)
            except Exception:
                continue
        return manifests

    def list_by_date(self, date: str) -> dict:
        organization = self.load_organization()
        manifest = self._read_manifest(self._manifest_path(date), date)
        manifest["assets"] = [
            self._with_output_metadata(asset, date, organization) for asset in manifest.get("assets", [])
        ]
        return manifest

    def find_blob(self, digest: str) -> Path | None:
        root = self.paths.media_dir / "blobs" / "sha256" / digest[:2] / digest[2:4]
        if not root.exists():
            return None
        matches = list(root.glob(f"{digest}.*"))
        return matches[0] if matches else None

    def find_asset(self, media_ref: str = "", date: str = "", original_name: str = "") -> dict | None:
        ref = (media_ref or "").strip()
        digest = self._extract_digest(ref)
        manifests = [self.list_by_date(date)] if date else self.list_manifests()
        for manifest in manifests:
            for asset in manifest.get("assets", []):
                if digest and asset.get("sha256") == digest:
                    return asset
                if ref and ref in {asset.get("url"), asset.get("path"), asset.get("sha256")}:
                    return asset
                if original_name and asset.get("original_name") == original_name:
                    return asset
        if digest:
            blob = self.find_blob(digest)
            if blob:
                asset = {
                    "sha256": digest,
                    "path": str(blob),
                    "url": f"/media/blobs/{digest}",
                    "original_name": blob.name,
                    "date": date,
                }
                asset.update(self._file_metadata(blob))
                return asset
        return None

    def storage_summary(self) -> dict:
        root = self.paths.media_dir / "blobs" / "sha256"
        total = 0
        count = 0
        if root.exists():
            for path in root.glob("*/*/*"):
                if path.is_file():
                    total += path.stat().st_size
                    count += 1
        return {"bytes": total, "count": count, "label": self._format_bytes(total)}

    def count_saved_since(self, hours: int = 12) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
        total = 0
        for manifest in self.list_manifests():
            for asset in manifest.get("assets", []):
                saved_at = str(asset.get("saved_at") or "")
                if not saved_at:
                    continue
                try:
                    saved_time = datetime.fromisoformat(saved_at)
                except ValueError:
                    continue
                if saved_time.tzinfo is None:
                    saved_time = saved_time.replace(tzinfo=timezone.utc)
                if saved_time >= cutoff:
                    total += 1
        return total

    def load_organization(self) -> dict:
        path = self._organization_path()
        if not path.exists():
            return {"folders": [], "asset_locations": {}, "trash": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        folders = [self._normalize_folder(item) for item in data.get("folders", []) if isinstance(item, dict)]
        return {
            "folders": folders,
            "asset_locations": data.get("asset_locations", {}) if isinstance(data.get("asset_locations"), dict) else {},
            "trash": data.get("trash", []) if isinstance(data.get("trash"), list) else [],
        }

    def create_folder(self, name: str = "", tags: list[str] | None = None, note: str = "") -> dict:
        organization = self.load_organization()
        folder_id = f"folder-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        folder = {
            "id": folder_id,
            "name": (name or "新建文件夹").strip() or "新建文件夹",
            "tags": [str(item).strip() for item in (tags or []) if str(item).strip()],
            "note": (note or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "trashed": False,
        }
        organization["folders"].append(folder)
        self.save_organization(organization)
        return folder

    def update_folder(self, folder_id: str, name: str = "", tags: list[str] | None = None, note: str = "") -> dict:
        organization = self.load_organization()
        folder = next((item for item in organization["folders"] if item.get("id") == folder_id), None)
        if not folder:
            raise ValueError("Folder not found")
        folder["name"] = (name or folder.get("name") or "新建文件夹").strip() or "新建文件夹"
        folder["tags"] = [str(item).strip() for item in (tags or []) if str(item).strip()]
        folder["note"] = (note or "").strip()
        self.save_organization(organization)
        return self._normalize_folder(folder)

    def move_asset_to_folder(self, digest: str, folder_id: str) -> dict:
        organization = self.load_organization()
        if folder_id and not any(item["id"] == folder_id and not item.get("trashed") for item in organization["folders"]):
            raise ValueError("Folder not found")
        digest = self._extract_digest(digest)
        if not digest:
            raise ValueError("Media asset not found")
        organization["asset_locations"][digest] = folder_id
        organization["trash"] = [
            item for item in organization["trash"] if not (item.get("type") == "asset" and item.get("id") == digest)
        ]
        self.save_organization(organization)
        return self.load_organization()

    def trash_item(self, item_type: str, item_id: str) -> dict:
        organization = self.load_organization()
        if item_type == "asset":
            item_id = self._extract_digest(item_id)
            if not item_id:
                raise ValueError("Media asset not found")
        elif item_type == "folder":
            folder = next((item for item in organization["folders"] if item["id"] == item_id), None)
            if not folder:
                raise ValueError("Folder not found")
            folder["trashed"] = True
            for digest, folder_id in organization["asset_locations"].items():
                if folder_id == item_id and not any(
                    item.get("type") == "asset" and item.get("id") == digest for item in organization["trash"]
                ):
                    organization["trash"].append(
                        {"type": "asset", "id": digest, "trashed_at": datetime.now(timezone.utc).isoformat()}
                    )
        else:
            raise ValueError("Unsupported item type")
        if not any(item.get("type") == item_type and item.get("id") == item_id for item in organization["trash"]):
            organization["trash"].append(
                {"type": item_type, "id": item_id, "trashed_at": datetime.now(timezone.utc).isoformat()}
            )
        self.save_organization(organization)
        return self.load_organization()

    def restore_item(self, item_type: str, item_id: str) -> dict:
        organization = self.load_organization()
        organization["trash"] = [
            item for item in organization["trash"] if not (item.get("type") == item_type and item.get("id") == item_id)
        ]
        if item_type == "folder":
            for folder in organization["folders"]:
                if folder["id"] == item_id:
                    folder["trashed"] = False
            folder_assets = [digest for digest, folder_id in organization["asset_locations"].items() if folder_id == item_id]
            organization["trash"] = [
                item
                for item in organization["trash"]
                if not (item.get("type") == "asset" and item.get("id") in folder_assets)
            ]
        self.save_organization(organization)
        return self.load_organization()

    def delete_item(self, item_type: str, item_id: str) -> dict:
        organization = self.load_organization()
        if item_type == "asset":
            digest = self._extract_digest(item_id)
            if not digest:
                raise ValueError("Media asset not found")
            self._delete_asset_everywhere(digest, strict=False)
            organization["asset_locations"].pop(digest, None)
            organization["trash"] = [
                item for item in organization["trash"] if not (item.get("type") == "asset" and item.get("id") == digest)
            ]
        elif item_type == "folder":
            folder_assets = [
                digest for digest, folder_id in organization["asset_locations"].items() if folder_id == item_id
            ]
            for digest in folder_assets:
                self._delete_asset_everywhere(digest, strict=False)
                organization["asset_locations"].pop(digest, None)
            organization["folders"] = [folder for folder in organization["folders"] if folder.get("id") != item_id]
            organization["trash"] = [
                item
                for item in organization["trash"]
                if not (
                    (item.get("type") == "folder" and item.get("id") == item_id)
                    or (item.get("type") == "asset" and item.get("id") in folder_assets)
                )
            ]
        else:
            raise ValueError("Unsupported item type")
        self.save_organization(organization)
        return self.load_organization()

    def update_asset_note(self, digest: str, note: str = "") -> dict:
        digest = self._extract_digest(digest)
        if not digest:
            raise ValueError("Media asset not found")
        updated: dict | None = None
        root = self.paths.media_dir / "by-date"
        if root.exists():
            for path in root.glob("*/*/*/manifest.json"):
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                changed = False
                for asset in manifest.get("assets", []):
                    if asset.get("sha256") == digest:
                        asset["note"] = (note or "").strip()
                        updated = asset
                        changed = True
                if changed:
                    atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2))
        if not updated:
            raise ValueError("Media asset not found")
        return updated

    def save_organization(self, organization: dict) -> None:
        path = self._organization_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = {
            "folders": [self._normalize_folder(item) for item in organization.get("folders", [])],
            "asset_locations": organization.get("asset_locations", {}),
            "trash": organization.get("trash", []),
        }
        atomic_write_text(path, json.dumps(normalized, ensure_ascii=False, indent=2))

    def _organization_path(self) -> Path:
        return self.paths.media_dir / "organization.json"

    def _delete_asset_everywhere(self, digest: str, strict: bool = True) -> None:
        found = False
        root = self.paths.media_dir / "by-date"
        if root.exists():
            for path in root.glob("*/*/*/manifest.json"):
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                before = len(manifest.get("assets", []))
                manifest["assets"] = [
                    item for item in manifest.get("assets", []) if item.get("sha256") != digest
                ]
                if len(manifest["assets"]) != before:
                    found = True
                    atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2))
        blob = self.find_blob(digest)
        if blob:
            blob.unlink(missing_ok=True)
            found = True
        if strict and not found:
            raise ValueError("Media asset not found")

    def _normalize_folder(self, folder: dict) -> dict:
        return {
            "id": str(folder.get("id") or "").strip(),
            "name": str(folder.get("name") or "新建文件夹").strip() or "新建文件夹",
            "tags": [str(item).strip() for item in folder.get("tags", []) if str(item).strip()] if isinstance(folder.get("tags"), list) else [],
            "note": str(folder.get("note") or "").strip(),
            "created_at": str(folder.get("created_at") or ""),
            "trashed": bool(folder.get("trashed", False)),
        }

    def _with_output_metadata(self, asset: dict, date: str, organization: dict | None = None) -> dict:
        output = dict(asset)
        organization = organization or self.load_organization()
        output.setdefault("date", date)
        blob = self.find_blob(output.get("sha256", ""))
        if blob:
            output["path"] = str(blob)
            output.update({key: value for key, value in self._file_metadata(blob).items() if output.get(key) in ("", None, 0)})
        output.setdefault("note", "")
        output.setdefault("url", f"/media/blobs/{output.get('sha256', '')}")
        digest = output.get("sha256", "")
        output["folder_id"] = organization.get("asset_locations", {}).get(digest, "")
        output["trashed"] = any(item.get("type") == "asset" and item.get("id") == digest for item in organization.get("trash", []))
        return output

    def _file_metadata(self, path: Path) -> dict:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        width = 0
        height = 0
        if Image is not None and mime_type.startswith("image/"):
            try:
                with Image.open(path) as image:
                    width, height = image.size
                    if image.get_format_mimetype():
                        mime_type = image.get_format_mimetype()
            except Exception:
                width = 0
                height = 0
        orientation = "landscape" if width >= height and width and height else "portrait" if width and height else "file"
        return {
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "mime_type": mime_type,
            "width": width,
            "height": height,
            "orientation": orientation,
            "is_image": mime_type.startswith("image/"),
        }

    def _extract_digest(self, value: str) -> str:
        for part in value.replace("\\", "/").split("/"):
            candidate = part.split(".")[0]
            if len(candidate) == 64 and all(char in "0123456789abcdefABCDEF" for char in candidate):
                return candidate.lower()
        if len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value):
            return value.lower()
        return ""

    def _different_paths(self, left: Path, right: Path) -> bool:
        try:
            return left.resolve() != right.resolve()
        except Exception:
            return str(left) != str(right)

    def _format_bytes(self, value: int) -> str:
        size = float(value)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{value} B"
