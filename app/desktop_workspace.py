"""Presentation-only native desktop primitives. No persistence or action authority."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter


class WorkspaceSplitter(QSplitter):
    """Two resizable panes on desktop, local selection on compact windows."""
    def __init__(self):
        super().__init__(Qt.Horizontal)
        self._selected = 0
        self._compact = False
        self._wide_sizes = [440, 460]
        self.setChildrenCollapsible(False)
        self.setHandleWidth(12)
        self.setStyleSheet("QSplitter::handle { background: transparent; }")
        self.splitterMoved.connect(self._remember_wide_sizes)

    def _remember_wide_sizes(self, position, index):
        if not self._compact:
            self._wide_sizes = self.sizes()

    def set_compact(self, compact):
        if compact == self._compact:
            return
        # A resize can already compress these sizes before this callback runs.
        # Remember the user's drag, not the transient compact layout geometry.
        self._compact = compact
        self.setCurrentIndex(self._selected)
        if not compact:
            self.setSizes(self._wide_sizes)

    def setCurrentIndex(self, index):
        self._selected = index
        for number in range(self.count()):
            self.widget(number).setVisible(not self._compact or number == index)

    def currentIndex(self):
        return self._selected
