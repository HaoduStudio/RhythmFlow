from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QListWidget, QListWidgetItem


logger = logging.getLogger(__name__)


class FileDropList(QListWidget):
    paths_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(180)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        logger.info("Dropped %d local file(s)", len(paths))
        self.add_paths(paths)
        event.acceptProposedAction()

    def add_paths(self, paths: list[str]) -> None:
        existing = set(self.paths())
        changed = False
        for raw_path in paths:
            path = str(Path(raw_path))
            if not path or path in existing:
                continue
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.addItem(item)
            existing.add(path)
            changed = True
        if changed:
            logger.info("Video list changed; added files")
            self.paths_changed.emit()

    def remove_selected(self) -> None:
        logger.info("Removing %d selected file(s)", len(self.selectedItems()))
        for item in self.selectedItems():
            self.takeItem(self.row(item))
        self.paths_changed.emit()

    def clear_paths(self) -> None:
        logger.info("Clearing %d file(s) from list", self.count())
        self.clear()
        self.paths_changed.emit()

    def paths(self) -> list[str]:
        values: list[str] = []
        for row in range(self.count()):
            item = self.item(row)
            values.append(item.data(Qt.ItemDataRole.UserRole))
        return values
